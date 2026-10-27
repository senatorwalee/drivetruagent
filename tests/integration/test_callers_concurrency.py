"""Two calls from one number at the same moment. These commit for real, on
separate connections, so they live apart from tests that roll back."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.db.models import Caller
from drivetruagent.enums import Channel
from drivetruagent.ordering.sessions import open_session


async def test_simultaneous_calls_from_one_number_are_both_counted(
    committing_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Checking for the caller first and inserting second would make one of
    these fail on the unique number; the upsert counts both."""

    async def ring() -> None:
        async with committing_sessions.begin() as db:
            await open_session(db, channel=Channel.PHONE, caller_phone="+14165550123")

    await asyncio.gather(*(ring() for _ in range(5)))

    async with committing_sessions() as db:
        callers = (await db.execute(select(Caller))).scalars().all()
    assert [(c.phone_e164, c.call_count) for c in callers] == [("+14165550123", 5)]
