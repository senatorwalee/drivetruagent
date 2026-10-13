"""The backend acting on its own when a session's clock runs out.

Nothing here asks the model anything. What is due is decided by plain
arithmetic in `ordering.timers`, and what the customer hears is fixed text,
so a timer still works when the model is slow or unreachable.

`sweep` is run once a second by a background task. Any number of processes
may run it at once: each session is handled under its row lock, and the
decision is made again once the lock is held.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.agent import events
from drivetruagent.agent.messages import AssistantMessage
from drivetruagent.db.models import CustomerSession
from drivetruagent.enums import (
    ACTIVE_SESSION_STATES,
    FINAL_SESSION_STATES,
    AgentEventKind,
    Language,
)
from drivetruagent.ordering.inventory import release_expired
from drivetruagent.ordering.sessions import clock_of, lock_session
from drivetruagent.ordering.timers import TimerAction, TimerSettings, due_action
from drivetruagent.ordering.views import DEFAULT_LANGUAGE
from drivetruagent.policy import gateway

logger = logging.getLogger(__name__)

STILL_THERE = {
    Language.EN: "Are you still there?",
    Language.FR: "Êtes-vous toujours là?",
}
HANDING_OFF = {
    Language.EN: "A team member will help you finish your order. One moment, please.",
    Language.FR: (
        "Un membre de l'équipe va vous aider à terminer votre commande. "
        "Un instant, s'il vous plaît."
    ),
}


@dataclass(frozen=True)
class SweepReport:
    """What one pass over the active sessions did."""

    nudged: int = 0
    abandoned: int = 0
    handed_off: int = 0
    # Sessions that could not be handled. The rest were not held up by them.
    failed: int = 0
    reservations_released: int = 0


async def enforce(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    now: datetime,
    timers: TimerSettings,
    tax_rate: Decimal,
) -> TimerAction:
    """Do whatever is due for one session, and say what it was.

    Takes the session's lock and decides under it, so if two callers get here
    together the second finds the job already done. Does not commit.
    """
    customer_session = await lock_session(db, session_id)
    if customer_session.state in FINAL_SESSION_STATES:
        return TimerAction.NONE
    action = due_action(clock_of(customer_session), now, timers)

    if action is TimerAction.NUDGE:
        silent_for = now - customer_session.last_activity_at
        turn_id = await _record(db, customer_session, action, "silence", silent_for)
        await _say(db, customer_session, turn_id, STILL_THERE)
        customer_session.nudged_at = now
        await db.flush()
    elif action is TimerAction.ABANDON:
        silent_for = now - customer_session.last_activity_at
        await _record(db, customer_session, action, gateway.SILENCE_TIMEOUT, silent_for)
        await gateway.abandon(db, session_id, tax_rate=tax_rate)
    elif action is TimerAction.HAND_OFF:
        await _hand_off(db, customer_session, now, tax_rate)
    return action


async def hand_off_if_over_time(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    now: datetime,
    timers: TimerSettings,
    tax_rate: Decimal,
) -> str | None:
    """Called as a customer turn starts. If the session has already run too
    long, hand it off and return what to tell the customer; otherwise None.

    This is what makes the length limit hold even if no sweep is running.
    Unlike a sweep it applies mid-turn: the turn it is part of has only just
    begun, and must not go ahead.
    """
    customer_session = await lock_session(db, session_id)
    if customer_session.state in FINAL_SESSION_STATES:
        return None
    if clock_of(customer_session).running_time(now) < timers.max_duration:
        return None
    return await _hand_off(db, customer_session, now, tax_rate)


async def sweep(
    db: AsyncSession, *, now: datetime, timers: TimerSettings, tax_rate: Decimal
) -> SweepReport:
    """One pass: apply what is due to every session in progress, then tidy
    up stock holds that have expired.

    Each session is committed on its own, so its lock is held only briefly
    and one session that fails does not stop the others.
    """
    session_ids = (
        await db.execute(
            select(CustomerSession.id)
            .where(
                CustomerSession.state.in_(ACTIVE_SESSION_STATES),
                CustomerSession.paused_at.is_(None),
            )
            .order_by(CustomerSession.started_at)
        )
    ).scalars()
    counts = dict.fromkeys(TimerAction, 0)
    failed = 0
    for session_id in list(session_ids):
        try:
            action = await enforce(
                db, session_id, now=now, timers=timers, tax_rate=tax_rate
            )
            await db.commit()
        except Exception:
            logger.exception("Could not apply timers to session %s", session_id)
            await db.rollback()
            failed += 1
        else:
            counts[action] += 1

    released = await release_expired(db)
    await db.commit()
    return SweepReport(
        nudged=counts[TimerAction.NUDGE],
        abandoned=counts[TimerAction.ABANDON],
        handed_off=counts[TimerAction.HAND_OFF],
        failed=failed,
        reservations_released=released,
    )


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float,
    timers: TimerSettings,
    tax_rate: Decimal,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Sweep, wait, repeat, until cancelled.

    A sweep that fails is logged and the next one runs as usual: a database
    blip must not stop timers for the rest of the day.
    """
    while True:
        try:
            async with session_factory() as db:
                await sweep(db, now=clock(), timers=timers, tax_rate=tax_rate)
        except Exception:
            logger.exception("A timer sweep failed")
        await asyncio.sleep(interval_seconds)


# --- Helpers ----------------------------------------------------------------


async def _hand_off(
    db: AsyncSession,
    customer_session: CustomerSession,
    now: datetime,
    tax_rate: Decimal,
) -> str:
    running_for = clock_of(customer_session).running_time(now)
    turn_id = await _record(
        db, customer_session, TimerAction.HAND_OFF, gateway.TIME_LIMIT, running_for
    )
    sentence = await _say(db, customer_session, turn_id, HANDING_OFF)
    await gateway.time_out(db, customer_session.id, tax_rate=tax_rate)
    return sentence


async def _record(
    db: AsyncSession,
    customer_session: CustomerSession,
    action: TimerAction,
    rule: str,
    after: timedelta,
) -> uuid.UUID:
    """Write the timer event. Returns the turn id the rest of it shares."""
    turn_id = uuid.uuid4()
    await events.record(
        db,
        customer_session.id,
        turn_id,
        AgentEventKind.TIMER,
        {
            "action": action.value,
            "rule": rule,
            "after_seconds": round(after.total_seconds()),
        },
    )
    return turn_id


async def _say(
    db: AsyncSession,
    customer_session: CustomerSession,
    turn_id: uuid.UUID,
    sentences: dict[Language, str],
) -> str:
    sentence = sentences[customer_session.language or DEFAULT_LANGUAGE]
    await events.record_message(
        db, customer_session.id, turn_id, AssistantMessage(sentence)
    )
    return sentence
