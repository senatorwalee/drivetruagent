"""Writing each session's summary once it has ended, and reading them back.

A background task finds sessions that ended recently and have no summary,
and writes one for each: facts read from the log, words from the model if
they pass the checks in agent/summary.py, code's own otherwise. It runs
apart from the timekeeper, so a slow model never holds up a timer, and apart
from the conversation, so it never slows a turn.

No transaction is held open while the model is asked: the facts are read and
that transaction ended first. Two processes writing the same session's
summary at once waste a model call; the second insert does nothing.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from drivetruagent.agent.model import ModelClient
from drivetruagent.agent.summary import (
    SUMMARY_INSTRUCTIONS,
    WRITE_SUMMARY,
    HandoffFact,
    OrderFact,
    OrderLineFact,
    SessionFacts,
    SessionInfo,
    SummarySettings,
    check_words,
    facts_of,
    fallback_gist,
    prompt_of,
    read_reply,
    transcript_of,
)
from drivetruagent.db.models import (
    AgentEvent,
    CustomerSession,
    Handoff,
    MenuItem,
    Order,
    SessionSummary,
)
from drivetruagent.enums import Channel, Language, Mood, SessionState, SummaryAuthor
from drivetruagent.ordering.errors import (
    SessionInProgress,
    SessionNotFound,
    SummaryNotFound,
)
from drivetruagent.policy.handoff_package import LoggedEvent, reason_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Gathered:
    """Everything a summary is written from."""

    facts: SessionFacts
    transcript: list[str]
    # Every item's name in every language, for checking the model's words.
    menu_names: list[str]


@dataclass(frozen=True)
class Written:
    """A summary's words, and who wrote them."""

    gist: str
    follow_up: str | None
    mood: Mood | None
    written_by: SummaryAuthor
    model: str | None = None
    rejected_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


async def summarize(
    db: AsyncSession,
    session_id: uuid.UUID,
    model: ModelClient | None,
    *,
    settings: SummarySettings,
    rewrite: bool = False,
) -> bool:
    """Write one session's summary and commit it. Returns False if it already
    had one (and `rewrite` was not asked for), True otherwise.

    Raises SessionNotFound, or SessionInProgress if it has not ended.
    """
    gathered = await gather(db, session_id)
    # Nothing is held while the model thinks.
    await db.commit()
    written = await write(gathered, model, timeout_seconds=settings.timeout_seconds)
    stored = await store(db, session_id, gathered.facts, written, rewrite=rewrite)
    await db.commit()
    return stored


async def gather(db: AsyncSession, session_id: uuid.UUID) -> Gathered:
    session = await db.get(CustomerSession, session_id, populate_existing=True)
    if session is None:
        raise SessionNotFound(session_id)
    if session.ended_at is None:
        raise SessionInProgress(session.state)
    rows = await db.execute(
        select(
            AgentEvent.kind,
            AgentEvent.payload,
            AgentEvent.created_at,
            AgentEvent.turn_id,
        )
        .where(AgentEvent.session_id == session_id)
        .order_by(AgentEvent.seq)
    )
    events = [LoggedEvent(kind, payload, at, turn) for kind, payload, at, turn in rows]
    info = SessionInfo(
        outcome=session.state,
        end_reason=session.end_reason,
        channel=session.channel,
        language=session.language,
        started_at=session.started_at,
        ended_at=session.ended_at,
        abuse_strikes=session.abuse_strikes,
        confusion_strikes=session.confusion_strikes,
    )
    facts = facts_of(
        events,
        info,
        order=await _order_fact(db, session_id),
        handoff=await _handoff_fact(db, session_id),
    )
    names = (await db.execute(select(MenuItem.name_i18n))).scalars()
    menu_names = sorted(
        {name for names_by_language in names for name in names_by_language.values()}
    )
    return Gathered(facts, transcript_of(events), menu_names)


async def write(
    gathered: Gathered, model: ModelClient | None, *, timeout_seconds: float
) -> Written:
    """The summary's words: the model's if they pass the checks, code's if
    not. Never raises: a broken model only costs the nicer wording."""
    facts = gathered.facts
    own = Written(fallback_gist(facts), None, None, SummaryAuthor.TEMPLATE)
    if facts.customer_turns == 0:
        # Nothing was said: there is nothing for a model to add.
        return own
    if model is None:
        return replace(own, rejected_reason="no_model")
    try:
        async with asyncio.timeout(timeout_seconds):
            turn = await model.complete(
                instructions=SUMMARY_INSTRUCTIONS,
                messages=[prompt_of(facts, gathered.transcript)],
                tools=[WRITE_SUMMARY],
            )
    except TimeoutError:
        logger.warning("The summary model took over %ss", timeout_seconds)
        return replace(own, rejected_reason="model_timed_out")
    except Exception:
        logger.exception("The summary model failed")
        return replace(own, rejected_reason="model_failed")
    asked = replace(
        own,
        model=turn.model or None,
        input_tokens=turn.usage.input_tokens if turn.usage else None,
        output_tokens=turn.usage.output_tokens if turn.usage else None,
    )
    words = read_reply(turn)
    if words is None:
        return replace(asked, rejected_reason="no_summary_in_reply")
    for text in (words.gist, words.follow_up or ""):
        problem = check_words(
            text,
            facts=facts,
            transcript=gathered.transcript,
            menu_names=gathered.menu_names,
        )
        if problem is not None:
            logger.info("A model summary was refused: %s", problem)
            return replace(asked, rejected_reason=problem)
    return replace(
        asked,
        gist=words.gist,
        follow_up=words.follow_up,
        mood=words.mood,
        written_by=SummaryAuthor.MODEL,
    )


async def store(
    db: AsyncSession,
    session_id: uuid.UUID,
    facts: SessionFacts,
    written: Written,
    *,
    rewrite: bool,
) -> bool:
    """Insert the summary, or replace it if `rewrite`. False if one was
    already there and was kept. Does not commit."""
    values = {
        "facts": facts.model_dump(mode="json"),
        "gist": written.gist,
        "follow_up": written.follow_up,
        "mood": written.mood,
        "written_by": written.written_by,
        "model": written.model,
        "rejected_reason": written.rejected_reason,
        "input_tokens": written.input_tokens,
        "output_tokens": written.output_tokens,
    }
    statement = insert(SessionSummary).values(session_id=session_id, **values)
    if rewrite:
        statement = statement.on_conflict_do_update(
            index_elements=[SessionSummary.session_id],
            set_={**values, "updated_at": func.now()},
        )
    else:
        statement = statement.on_conflict_do_nothing(
            index_elements=[SessionSummary.session_id]
        )
    result = await db.execute(statement.returning(SessionSummary.id))
    return result.scalar_one_or_none() is not None


async def pending(
    db: AsyncSession, *, now: datetime, lookback: timedelta, limit: int
) -> list[uuid.UUID]:
    """Sessions that ended within `lookback` and have no summary, oldest first."""
    summarised = exists().where(SessionSummary.session_id == CustomerSession.id)
    rows = await db.execute(
        select(CustomerSession.id)
        .where(CustomerSession.ended_at >= now - lookback, ~summarised)
        .order_by(CustomerSession.ended_at, CustomerSession.id)
        .limit(limit)
    )
    return list(rows.scalars())


async def summarize_pending(
    session_factory: async_sessionmaker[AsyncSession],
    model: ModelClient | None,
    *,
    now: datetime,
    settings: SummarySettings,
) -> int:
    """One pass: summarise up to a batch of waiting sessions, each in its own
    database session so one that fails does not stop the rest. Returns how
    many were written."""
    async with session_factory() as db:
        waiting = await pending(
            db, now=now, lookback=settings.lookback, limit=settings.batch
        )
    written = 0
    for session_id in waiting:
        try:
            async with session_factory() as db:
                written += await summarize(db, session_id, model, settings=settings)
        except Exception:
            logger.exception("Could not summarise session %s", session_id)
    return written


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    model: ModelClient | None,
    *,
    interval_seconds: float,
    settings: SummarySettings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Summarise, wait, repeat, until cancelled. A pass that fails is logged
    and the next one runs as usual."""
    while True:
        try:
            await summarize_pending(
                session_factory, model, now=clock(), settings=settings
            )
        except Exception:
            logger.exception("A summary pass failed")
        await asyncio.sleep(interval_seconds)


# --- Reading ----------------------------------------------------------------


class SummaryView(BaseModel):
    """One row of the conversations list."""

    session_id: uuid.UUID
    lane: str
    channel: Channel
    language: Language | None
    outcome: SessionState
    started_at: datetime
    ended_at: datetime
    gist: str
    follow_up: str | None
    mood: Mood | None
    written_by: SummaryAuthor
    order_number: int | None
    total_cents: int | None
    handoff_title: str | None
    written_at: datetime


class SummaryDetail(SummaryView):
    """A summary with everything behind it."""

    facts: SessionFacts
    model: str | None
    # Why the model's words were not used, if they were not.
    rejected_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


async def list_summaries(
    db: AsyncSession, *, outcome: SessionState | None = None, limit: int = 50
) -> list[SummaryView]:
    """The most recent summaries, latest session first."""
    statement = (
        select(SessionSummary, CustomerSession.lane)
        .join(CustomerSession, SessionSummary.session_id == CustomerSession.id)
        .order_by(CustomerSession.ended_at.desc(), CustomerSession.id)
        .limit(limit)
    )
    if outcome is not None:
        statement = statement.where(CustomerSession.state == outcome)
    rows = await db.execute(statement)
    return [SummaryView.model_validate(_view(summary, lane)) for summary, lane in rows]


async def get_summary(db: AsyncSession, session_id: uuid.UUID) -> SummaryDetail:
    """Raises SummaryNotFound if the session has none (yet)."""
    row = (
        await db.execute(
            select(SessionSummary, CustomerSession.lane)
            .join(CustomerSession, SessionSummary.session_id == CustomerSession.id)
            .where(SessionSummary.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise SummaryNotFound(session_id)
    summary, lane = row
    return SummaryDetail.model_validate(
        {
            **_view(summary, lane),
            "facts": summary.facts,
            "model": summary.model,
            "rejected_reason": summary.rejected_reason,
            "input_tokens": summary.input_tokens,
            "output_tokens": summary.output_tokens,
        }
    )


def _view(summary: SessionSummary, lane: str) -> dict[str, object]:
    facts = SessionFacts.model_validate(summary.facts)
    return {
        "session_id": summary.session_id,
        "lane": lane,
        "channel": facts.channel,
        "language": facts.language,
        "outcome": facts.outcome,
        "started_at": facts.started_at,
        "ended_at": facts.ended_at,
        "gist": summary.gist,
        "follow_up": summary.follow_up,
        "mood": summary.mood,
        "written_by": summary.written_by,
        "order_number": facts.order.order_number if facts.order else None,
        "total_cents": facts.order.total_cents if facts.order else None,
        "handoff_title": facts.handoff.title if facts.handoff else None,
        "written_at": summary.updated_at,
    }


# --- Helpers ----------------------------------------------------------------


async def _order_fact(db: AsyncSession, session_id: uuid.UUID) -> OrderFact | None:
    order = (
        await db.execute(
            select(Order)
            .where(Order.session_id == session_id)
            .options(selectinload(Order.lines))
        )
    ).scalar_one_or_none()
    if order is None:
        return None
    return OrderFact(
        order_number=order.order_number,
        lines=[
            OrderLineFact(quantity=line.quantity, item_name=line.item_name)
            for line in order.lines
        ],
        total_cents=order.total_cents,
    )


async def _handoff_fact(db: AsyncSession, session_id: uuid.UUID) -> HandoffFact | None:
    reason = (
        await db.execute(select(Handoff.reason).where(Handoff.session_id == session_id))
    ).scalar_one_or_none()
    return (
        None
        if reason is None
        else HandoffFact(reason=reason, title=reason_title(reason))
    )
