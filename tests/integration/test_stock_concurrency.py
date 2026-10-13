"""Cars racing for the same stock, for real: separate connections, real commits.

These are the tests that show the row lock does its job. With the lock
removed from `_lock_inventory`, they fail.
"""

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.db.models import Order, OrderLine
from drivetruagent.ordering.cart import add_item
from drivetruagent.ordering.errors import InsufficientStock
from drivetruagent.ordering.inventory import set_on_hand, stock_levels
from drivetruagent.ordering.orders import submit_order
from drivetruagent.ordering.sessions import open_session
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

TAX = Decimal("0.13")

type Sessions = async_sessionmaker[AsyncSession]


async def open_cars(
    sessions: Sessions, count: int, cookies_on_hand: int
) -> list[uuid.UUID]:
    """Load the menu, set the cookie stock, and open one session per lane."""
    async with sessions.begin() as db:
        await seed_menu(db, load_menu(MENU_PATH))
        await set_on_hand(db, "cookie", cookies_on_hand)
        return [(await open_session(db, f"lane-{n}")).id for n in range(count)]


async def try_to_add_a_cookie(sessions: Sessions, session_id: uuid.UUID) -> bool:
    try:
        async with sessions.begin() as db:
            await add_item(db, session_id, "cookie", tax_rate=TAX)
    except InsufficientStock:
        return False
    return True


async def try_to_submit(sessions: Sessions, session_id: uuid.UUID) -> bool:
    try:
        async with sessions.begin() as db:
            await submit_order(db, session_id, tax_rate=TAX)
    except InsufficientStock:
        return False
    return True


async def cookie_stock(sessions: Sessions) -> tuple[int, int]:
    async with sessions() as db:
        level = next(lv for lv in await stock_levels(db) if lv.item_code == "cookie")
        return level.on_hand, level.reserved


async def test_two_cars_racing_for_the_last_cookie(
    committing_sessions: Sessions,
) -> None:
    car_a, car_b = await open_cars(committing_sessions, 2, cookies_on_hand=1)

    results = await asyncio.gather(
        try_to_add_a_cookie(committing_sessions, car_a),
        try_to_add_a_cookie(committing_sessions, car_b),
    )

    assert sorted(results) == [False, True]
    assert await cookie_stock(committing_sessions) == (1, 1)


async def test_ten_cars_racing_for_three_cookies(
    committing_sessions: Sessions,
) -> None:
    cars = await open_cars(committing_sessions, 10, cookies_on_hand=3)

    results = await asyncio.gather(
        *(try_to_add_a_cookie(committing_sessions, car) for car in cars)
    )

    assert results.count(True) == 3
    assert results.count(False) == 7
    assert await cookie_stock(committing_sessions) == (3, 3)


async def test_no_more_is_sold_than_there_was(
    committing_sessions: Sessions,
) -> None:
    """Ten cars, three cookies: everyone adds, then everyone who got one submits,
    all at once. Exactly three orders exist and the stock ends at zero."""
    cars = await open_cars(committing_sessions, 10, cookies_on_hand=3)
    added = await asyncio.gather(
        *(try_to_add_a_cookie(committing_sessions, car) for car in cars)
    )
    winners = [car for car, got_one in zip(cars, added, strict=True) if got_one]

    submitted = await asyncio.gather(
        *(try_to_submit(committing_sessions, car) for car in winners)
    )

    assert submitted == [True, True, True]
    assert await cookie_stock(committing_sessions) == (0, 0)
    async with committing_sessions() as db:
        orders = (
            await db.execute(select(func.count()).select_from(Order))
        ).scalar_one()
        cookies_sold = (
            await db.execute(select(func.sum(OrderLine.quantity)))
        ).scalar_one()
    assert orders == 3
    assert cookies_sold == 3


async def test_a_car_adding_while_another_submits_cannot_oversell(
    committing_sessions: Sessions,
) -> None:
    """Car A holds the only cookie and submits while car B tries to add one."""
    car_a, car_b = await open_cars(committing_sessions, 2, cookies_on_hand=1)
    assert await try_to_add_a_cookie(committing_sessions, car_a)

    submitted, added = await asyncio.gather(
        try_to_submit(committing_sessions, car_a),
        try_to_add_a_cookie(committing_sessions, car_b),
    )

    assert submitted is True
    assert added is False
    assert await cookie_stock(committing_sessions) == (0, 0)
