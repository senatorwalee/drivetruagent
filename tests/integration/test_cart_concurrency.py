"""Changes to one cart arriving at the same moment.

These tests commit for real, on separate connections. They live apart from
test_cart.py on purpose: that module loads the menu inside a transaction it
later rolls back, and a committed load of the same menu would wait on it.
"""

import asyncio
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.ordering.cart import add_item, get_cart, remove_line
from drivetruagent.ordering.sessions import open_session
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

TAX = Decimal("0.13")


async def test_simultaneous_adds_get_distinct_line_numbers(
    committing_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Five tool calls landing at once.

    Without the row lock they would all read next_line_no = 1 and collide.
    """
    async with committing_sessions.begin() as db:
        await seed_menu(db, load_menu(MENU_PATH))
        session_id = (await open_session(db)).id

    async def add_cookie() -> None:
        async with committing_sessions.begin() as db:
            await add_item(db, session_id, "cookie", tax_rate=TAX)

    await asyncio.gather(*(add_cookie() for _ in range(5)))

    async with committing_sessions() as db:
        cart = await get_cart(db, session_id, tax_rate=TAX)
    assert [line.line_no for line in cart.lines] == [1, 2, 3, 4, 5]
    assert cart.version == 5


async def test_simultaneous_adds_and_removes_keep_the_version_in_step(
    committing_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with committing_sessions.begin() as db:
        await seed_menu(db, load_menu(MENU_PATH))
        session_id = (await open_session(db)).id
        await add_item(db, session_id, "cookie", tax_rate=TAX)
        await add_item(db, session_id, "apple_pie", tax_rate=TAX)

    async def add_water() -> None:
        async with committing_sessions.begin() as db:
            await add_item(db, session_id, "bottled_water", tax_rate=TAX)

    async def remove_first() -> None:
        async with committing_sessions.begin() as db:
            await remove_line(db, session_id, 1, tax_rate=TAX)

    await asyncio.gather(add_water(), remove_first(), add_water())

    async with committing_sessions() as db:
        cart = await get_cart(db, session_id, tax_rate=TAX)
    assert [line.line_no for line in cart.lines] == [2, 3, 4]
    # Two changes during setup, three at once: none was lost.
    assert cart.version == 5
