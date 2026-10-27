"""Session summaries with real concurrency: separate connections, real commits.

In a module of their own because they must not share a test with the
rollback-based fixtures: an open transaction there would block the clean-up
here.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.agent import events, summarizer
from drivetruagent.agent.messages import Message, ToolCall, UserMessage
from drivetruagent.agent.model import ModelTurn, ToolDefinition
from drivetruagent.agent.summary import SummarySettings
from drivetruagent.db.models import SessionSummary
from drivetruagent.enums import AgentEventKind, SessionState, SummaryAuthor
from drivetruagent.ordering.sessions import end_session, open_session

SETTINGS = SummarySettings()

type Sessions = async_sessionmaker[AsyncSession]


async def ended_session(sessions: Sessions, lane: str, said: str | None) -> uuid.UUID:
    """A session that ended without an order, after the customer said `said`."""
    async with sessions.begin() as db:
        session_id = (await open_session(db, lane)).id
        if said is not None:
            await events.record_message(db, session_id, uuid.uuid4(), UserMessage(said))
        await end_session(db, session_id, SessionState.ABANDONED, "silence")
    return session_id


async def summaries(sessions: Sessions) -> list[SessionSummary]:
    async with sessions() as db:
        return list((await db.execute(select(SessionSummary))).scalars())


class SlowModel:
    """Takes a moment to answer, so two writers overlap."""

    def __init__(self) -> None:
        self.asked = 0

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        self.asked += 1
        await asyncio.sleep(0.2)
        arguments = {
            "gist": "Left without ordering.",
            "follow_up": "",
            "mood": "neutral",
        }
        return ModelTurn(
            tool_calls=(ToolCall("s", "write_summary", arguments),), model="slow"
        )


async def test_a_pass_summarises_every_waiting_session(
    committing_sessions: Sessions,
) -> None:
    quiet = await ended_session(committing_sessions, "lane-1", None)
    spoke = await ended_session(committing_sessions, "lane-2", "Just looking")
    model = SlowModel()

    written = await summarizer.summarize_pending(
        committing_sessions, model, now=datetime.now(UTC), settings=SETTINGS
    )

    assert written == 2
    # Only the session where something was said went to the model.
    assert model.asked == 1
    by_session = {row.session_id: row for row in await summaries(committing_sessions)}
    assert by_session[quiet].written_by is SummaryAuthor.TEMPLATE
    assert by_session[spoke].written_by is SummaryAuthor.MODEL
    # The next pass finds nothing left to do.
    assert (
        await summarizer.summarize_pending(
            committing_sessions, model, now=datetime.now(UTC), settings=SETTINGS
        )
        == 0
    )


async def test_one_session_that_fails_does_not_stop_the_others(
    committing_sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = await ended_session(committing_sessions, "lane-1", None)
    fine = await ended_session(committing_sessions, "lane-2", None)
    real_gather = summarizer.gather

    async def gather(db: AsyncSession, session_id: uuid.UUID, **options: Any) -> Any:
        if session_id == broken:
            raise RuntimeError("bad row")
        return await real_gather(db, session_id, **options)

    monkeypatch.setattr(summarizer, "gather", gather)

    written = await summarizer.summarize_pending(
        committing_sessions, None, now=datetime.now(UTC), settings=SETTINGS
    )

    assert written == 1
    assert [row.session_id for row in await summaries(committing_sessions)] == [fine]


async def test_two_writers_at_once_leave_one_summary(
    committing_sessions: Sessions,
) -> None:
    session_id = await ended_session(committing_sessions, "lane-1", "Hello?")
    model = SlowModel()

    async def write() -> bool:
        async with committing_sessions() as db:
            return await summarizer.summarize(db, session_id, model, settings=SETTINGS)

    results = await asyncio.gather(write(), write())

    assert sorted(results) == [False, True]
    assert model.asked == 2
    async with committing_sessions() as db:
        count = await db.scalar(select(func.count()).select_from(SessionSummary))
    assert count == 1


async def test_the_background_task_carries_on_after_a_failed_pass(
    committing_sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    passes = 0
    second_pass = asyncio.Event()

    async def summarize_pending(*_: Any, **__: Any) -> int:
        nonlocal passes
        passes += 1
        if passes == 1:
            raise RuntimeError("database blip")
        second_pass.set()
        return 0

    monkeypatch.setattr(summarizer, "summarize_pending", summarize_pending)
    task = asyncio.create_task(
        summarizer.run_forever(
            committing_sessions, None, interval_seconds=0.01, settings=SETTINGS
        )
    )
    try:
        await asyncio.wait_for(second_pass.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert passes >= 2


async def test_the_agent_s_messages_are_what_the_model_is_shown(
    committing_sessions: Sessions,
) -> None:
    """A customer's words reach the summary as part of the conversation."""
    session_id = await ended_session(
        committing_sessions, "lane-1", "Do you have poutine?"
    )
    async with committing_sessions() as db:
        gathered = await summarizer.gather(db, session_id)
        kinds = [e.kind for e in await events.load_events(db, session_id)]

    assert kinds == [AgentEventKind.USER_MESSAGE]
    assert gathered.transcript == ["Customer: Do you have poutine?"]
