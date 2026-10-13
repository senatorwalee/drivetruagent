"""Starting and ending a customer's session, and keeping its clocks.

The clocks are plain timestamps on the session row. What they add up to
(a nudge, a timeout) is decided in `timers`, never here.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import Cart, CustomerSession
from drivetruagent.enums import FINAL_SESSION_STATES, Language, SessionState
from drivetruagent.ordering.errors import LaneBusy, SessionEnded, SessionNotFound
from drivetruagent.ordering.inventory import release_reservations
from drivetruagent.ordering.timers import (
    SessionClock,
    TimerSettings,
    deadlines,
)
from drivetruagent.ordering.views import SessionView, TimersView

ONE_ACTIVE_SESSION_PER_LANE = "uq_sessions_one_active_per_lane"


async def open_session(
    db: AsyncSession, lane: str = "lane-1", *, business_date: date | None = None
) -> CustomerSession:
    """Create a session and its empty cart. Raises LaneBusy if the lane is taken.

    The database's unique index decides whether the lane is free. Checking
    first and inserting second would let two cars through at the same moment.

    `business_date` is the restaurant's own calendar date, which decides the
    specials this session gets. Anything serving customers passes it. Left
    out, it is today's date in UTC, which is good enough where specials do
    not matter.
    """
    try:
        # A savepoint: if the insert is refused, only this block is undone and
        # the caller's transaction stays usable.
        async with db.begin_nested():
            customer_session = CustomerSession(
                lane=lane, business_date=business_date or datetime.now(UTC).date()
            )
            db.add(customer_session)
            await db.flush()
            db.add(Cart(session_id=customer_session.id))
            await db.flush()
    except IntegrityError as error:
        if ONE_ACTIVE_SESSION_PER_LANE in str(error.orig):
            raise LaneBusy(lane) from error
        raise
    return customer_session


async def lock_session(db: AsyncSession, session_id: uuid.UUID) -> CustomerSession:
    """Load a session and hold a row lock on it until the transaction ends.

    Every change to a session or its cart starts here. Two changes to the same
    session therefore run one after the other, never interleaved, so a tool
    call that arrives twice cannot hand out the same line number twice.
    """
    statement = (
        select(CustomerSession)
        .where(CustomerSession.id == session_id)
        .with_for_update()
        # Overwrite any copy already in memory with what the database holds now.
        .execution_options(populate_existing=True)
    )
    customer_session = (await db.execute(statement)).scalar_one_or_none()
    if customer_session is None:
        raise SessionNotFound(session_id)
    return customer_session


async def lock_active_session(
    db: AsyncSession, session_id: uuid.UUID
) -> CustomerSession:
    """Like lock_session, but refuses a session that has already ended."""
    customer_session = await lock_session(db, session_id)
    if customer_session.state in FINAL_SESSION_STATES:
        raise SessionEnded(customer_session.state)
    return customer_session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> CustomerSession:
    """Read a session without locking it. Raises SessionNotFound."""
    customer_session = await db.get(CustomerSession, session_id, populate_existing=True)
    if customer_session is None:
        raise SessionNotFound(session_id)
    return customer_session


async def set_language(
    db: AsyncSession, session_id: uuid.UUID, language: Language
) -> CustomerSession:
    """Set the language the customer is served in. Names in later cart
    snapshots, and in the order, follow it."""
    customer_session = await lock_active_session(db, session_id)
    customer_session.language = language
    await db.flush()
    return customer_session


async def end_session(
    db: AsyncSession, session_id: uuid.UUID, state: SessionState, reason: str
) -> CustomerSession:
    """Move a session to a final state. An ended session can never change again."""
    if state not in FINAL_SESSION_STATES:
        raise ValueError(f"{state.value} is not a final state")
    customer_session = await lock_active_session(db, session_id)
    customer_session.state = state
    customer_session.ended_at = datetime.now(UTC)
    customer_session.end_reason = reason
    if state is not SessionState.HANDED_OFF:
        # Nobody is coming back for this order. A handed-off session keeps its
        # stock, because a person is about to finish it.
        await release_reservations(db, session_id, reason=state.value)
    await db.flush()
    return customer_session


# --- Clocks -----------------------------------------------------------------


def clock_of(customer_session: CustomerSession) -> SessionClock:
    return SessionClock(
        started_at=customer_session.started_at,
        last_activity_at=customer_session.last_activity_at,
        nudged_at=customer_session.nudged_at,
        turn_started_at=customer_session.turn_started_at,
        paused_at=customer_session.paused_at,
        paused_seconds=customer_session.paused_seconds,
    )


def to_view(
    customer_session: CustomerSession, *, now: datetime, timers: TimerSettings
) -> SessionView:
    """A session as callers see it, with when its timers will next fire."""
    if customer_session.state in FINAL_SESSION_STATES:
        timers_view = TimersView(
            paused=False,
            paused_at=None,
            paused_seconds=customer_session.paused_seconds,
            nudge_at=None,
            closes_at=None,
            hand_off_at=None,
        )
    else:
        clock = clock_of(customer_session)
        due = deadlines(clock, now, timers)
        timers_view = TimersView(
            paused=clock.paused,
            paused_at=clock.paused_at,
            paused_seconds=clock.paused_seconds,
            nudge_at=due.nudge_at,
            closes_at=due.closes_at,
            hand_off_at=due.hand_off_at,
        )
    return SessionView(
        session_id=customer_session.id,
        lane=customer_session.lane,
        state=customer_session.state,
        language=customer_session.language,
        started_at=customer_session.started_at,
        ended_at=customer_session.ended_at,
        end_reason=customer_session.end_reason,
        business_date=customer_session.business_date,
        timers=timers_view,
    )


async def begin_turn(
    db: AsyncSession, session_id: uuid.UUID, *, now: datetime
) -> CustomerSession:
    """The customer has said something and the agent is about to answer.

    That is activity, so the silence clock starts again and any nudge is
    answered. The clocks then stop until `end_turn`: time the agent spends
    replying is not the customer being silent. A paused session resumes,
    because a customer speaking is a session that is under way.
    """
    customer_session = await lock_session(db, session_id)
    if customer_session.state in FINAL_SESSION_STATES:
        return customer_session
    _resume(customer_session, now)
    customer_session.last_activity_at = now
    customer_session.nudged_at = None
    customer_session.turn_started_at = now
    await db.flush()
    return customer_session


async def end_turn(db: AsyncSession, session_id: uuid.UUID, *, now: datetime) -> None:
    """The agent has finished replying. Silence is measured from now."""
    customer_session = await lock_session(db, session_id)
    customer_session.turn_started_at = None
    if customer_session.state not in FINAL_SESSION_STATES:
        customer_session.last_activity_at = now
    await db.flush()


async def pause(
    db: AsyncSession, session_id: uuid.UUID, *, now: datetime
) -> CustomerSession:
    """Stop the session's clocks, for a presenter who wants to talk.

    Pausing a session that is already paused changes nothing.
    """
    customer_session = await lock_active_session(db, session_id)
    if customer_session.paused_at is None:
        customer_session.paused_at = now
        await db.flush()
    return customer_session


async def resume(
    db: AsyncSession, session_id: uuid.UUID, *, now: datetime
) -> CustomerSession:
    """Start the clocks again. Time spent paused counts toward nothing, and
    the customer gets a fresh stretch of silence before any nudge."""
    customer_session = await lock_active_session(db, session_id)
    if _resume(customer_session, now):
        customer_session.last_activity_at = now
        customer_session.nudged_at = None
        await db.flush()
    return customer_session


def _resume(customer_session: CustomerSession, now: datetime) -> bool:
    """Add the pause just ended to the total. False if it was not paused."""
    if customer_session.paused_at is None:
        return False
    paused_for = max(0, round((now - customer_session.paused_at).total_seconds()))
    customer_session.paused_seconds += paused_for
    customer_session.paused_at = None
    return True
