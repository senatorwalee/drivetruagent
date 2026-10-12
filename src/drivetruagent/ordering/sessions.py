"""Starting and ending a customer's session."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import Cart, CustomerSession
from drivetruagent.enums import FINAL_SESSION_STATES, SessionState
from drivetruagent.ordering.errors import LaneBusy, SessionEnded, SessionNotFound

ONE_ACTIVE_SESSION_PER_LANE = "uq_sessions_one_active_per_lane"


async def open_session(db: AsyncSession, lane: str = "lane-1") -> CustomerSession:
    """Create a session and its empty cart. Raises LaneBusy if the lane is taken.

    The database's unique index decides whether the lane is free. Checking
    first and inserting second would let two cars through at the same moment.
    """
    try:
        # A savepoint: if the insert is refused, only this block is undone and
        # the caller's transaction stays usable.
        async with db.begin_nested():
            customer_session = CustomerSession(lane=lane)
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
    await db.flush()
    return customer_session
