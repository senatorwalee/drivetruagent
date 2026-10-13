"""Two people closing the same handoff at the same moment, for real: separate
connections, real commits.

In a module of its own because it must not share a test with the
rollback-based fixtures: an open transaction there would block the clean-up
here. With the row lock removed from `handoffs._load`, these fail.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.db.models import Handoff, Order
from drivetruagent.enums import HandoffOutcome, HandoffStatus
from drivetruagent.ordering.cart import add_item
from drivetruagent.ordering.inventory import set_on_hand, stock_levels
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy import gateway, handoffs
from drivetruagent.policy.errors import HandoffResolved
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

TAX = Decimal("0.13")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

type Sessions = async_sessionmaker[AsyncSession]


async def a_handed_off_order_of_two_cookies(sessions: Sessions) -> uuid.UUID:
    """Five cookies in stock, two in a cart, and the session handed off.
    Returns the handoff's id."""
    async with sessions.begin() as db:
        await seed_menu(db, load_menu(MENU_PATH))
        await set_on_hand(db, "cookie", 5)
        session_id = (await open_session(db)).id
        await add_item(db, session_id, "cookie", 2, tax_rate=TAX)
        await gateway.hand_to_human(db, session_id, "asked", tax_rate=TAX)
        return (await db.execute(select(Handoff.id))).scalar_one()


async def try_to_resolve(
    sessions: Sessions, handoff_id: uuid.UUID, outcome: HandoffOutcome
) -> HandoffOutcome | None:
    """The outcome if this caller was the one to resolve it, else None."""
    try:
        async with sessions.begin() as db:
            await handoffs.resolve(db, handoff_id, outcome, now=NOW, tax_rate=TAX)
    except HandoffResolved:
        return None
    return outcome


async def state_after(
    sessions: Sessions,
) -> tuple[HandoffStatus, object, int, int, int]:
    """(status, outcome, orders, cookies on hand, cookies reserved)."""
    async with sessions() as db:
        handoff = (await db.execute(select(Handoff))).scalar_one()
        orders = (
            await db.execute(select(func.count()).select_from(Order))
        ).scalar_one()
        level = next(lv for lv in await stock_levels(db) if lv.item_code == "cookie")
        return handoff.status, handoff.outcome, orders, level.on_hand, level.reserved


async def test_five_people_placing_the_same_order_place_it_once(
    committing_sessions: Sessions,
) -> None:
    handoff_id = await a_handed_off_order_of_two_cookies(committing_sessions)

    results = await asyncio.gather(
        *(
            try_to_resolve(committing_sessions, handoff_id, HandoffOutcome.ORDER_PLACED)
            for _ in range(5)
        )
    )

    assert results.count(HandoffOutcome.ORDER_PLACED) == 1
    assert results.count(None) == 4
    # One order, and the two cookies were taken from stock once, not five times.
    assert await state_after(committing_sessions) == (
        HandoffStatus.RESOLVED,
        HandoffOutcome.ORDER_PLACED,
        1,
        3,
        0,
    )


async def test_one_person_placing_while_another_cancels_ends_one_way_only(
    committing_sessions: Sessions,
) -> None:
    """Whoever gets there first decides. The stock and the order must agree
    with that, whichever it was."""
    handoff_id = await a_handed_off_order_of_two_cookies(committing_sessions)

    placed, cancelled = await asyncio.gather(
        try_to_resolve(committing_sessions, handoff_id, HandoffOutcome.ORDER_PLACED),
        try_to_resolve(committing_sessions, handoff_id, HandoffOutcome.CANCELLED),
    )

    winners = [outcome for outcome in (placed, cancelled) if outcome is not None]
    assert len(winners) == 1
    status, outcome, orders, on_hand, reserved = await state_after(committing_sessions)
    assert (status, outcome, reserved) == (HandoffStatus.RESOLVED, winners[0], 0)
    if outcome is HandoffOutcome.ORDER_PLACED:
        assert (orders, on_hand) == (1, 3)
    else:
        assert (orders, on_hand) == (0, 5)
