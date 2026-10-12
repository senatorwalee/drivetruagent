"""Checks that the db_session fixture really gives each test a clean database.

Both tests do the same thing: confirm a probe table is absent, then create it.
Whichever runs second proves the first one's work was rolled back.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def probe_table_exists(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT to_regclass('isolation_probe')"))
    return result.scalar_one() is not None


async def create_probe_table(session: AsyncSession) -> None:
    await session.execute(text("CREATE TABLE isolation_probe (id integer)"))
    # A commit inside a test must not escape the fixture's outer transaction.
    await session.commit()


async def test_first_test_starts_clean(db_session: AsyncSession) -> None:
    assert not await probe_table_exists(db_session)
    await create_probe_table(db_session)
    assert await probe_table_exists(db_session)


async def test_second_test_starts_clean(db_session: AsyncSession) -> None:
    assert not await probe_table_exists(db_session)
    await create_probe_table(db_session)
    assert await probe_table_exists(db_session)
