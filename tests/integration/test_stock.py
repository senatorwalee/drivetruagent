"""Stock holds, against a real database and the real menu.

The seed gives every item 100 on hand. Each test sets the level it needs.
Desserts, in menu order: apple_pie, sundae, cookie, soft_serve_cone.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import Inventory, MenuItem, Order, Reservation
from drivetruagent.enums import Language, ReservationStatus, SessionState
from drivetruagent.ordering.cart import (
    add_item,
    get_cart,
    remove_line,
    request_confirmation,
    update_line,
)
from drivetruagent.ordering.errors import InsufficientStock
from drivetruagent.ordering.inventory import (
    StockLevel,
    release_expired,
    set_on_hand,
    stock_levels,
)
from drivetruagent.ordering.menu import get_item_details, get_menu, search_menu
from drivetruagent.ordering.orders import submit_order
from drivetruagent.ordering.sessions import end_session, open_session
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import AddItem
from drivetruagent.policy.decisions import Decision, PolicyLimits
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

TAX = Decimal("0.13")

pytestmark = pytest.mark.usefixtures("menu")


@pytest.fixture
async def car_a(db_session: AsyncSession, menu: None) -> uuid.UUID:
    return (await open_session(db_session, "lane-a")).id


@pytest.fixture
async def car_b(db_session: AsyncSession, menu: None) -> uuid.UUID:
    return (await open_session(db_session, "lane-b")).id


async def add(db: AsyncSession, session_id: uuid.UUID, item: str, quantity: int = 1):
    return await add_item(db, session_id, item, quantity=quantity, tax_rate=TAX)


async def level(db: AsyncSession, item_code: str) -> StockLevel:
    return next(lv for lv in await stock_levels(db) if lv.item_code == item_code)


async def holds(db: AsyncSession, session_id: uuid.UUID) -> list[Reservation]:
    statement = (
        select(Reservation)
        .where(Reservation.session_id == session_id)
        .order_by(Reservation.created_at, Reservation.id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(statement)).scalars())


async def expire_holds(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Make a session's holds look as if their time ran out a minute ago."""
    await db.execute(
        update(Reservation)
        .where(Reservation.session_id == session_id)
        .values(expires_at=func.now() - timedelta(minutes=1))
    )


# --- Holding stock ----------------------------------------------------------


async def test_adding_an_item_holds_its_stock(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)

    await add(db_session, car_a, "cookie", 2)

    cookies = await level(db_session, "cookie")
    assert (cookies.on_hand, cookies.reserved, cookies.available) == (3, 2, 1)
    (hold,) = await holds(db_session, car_a)
    assert (hold.quantity, hold.status) == (2, ReservationStatus.ACTIVE)


async def test_asking_for_more_than_there_is_is_refused_and_changes_nothing(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)

    with pytest.raises(InsufficientStock) as error:
        await add(db_session, car_a, "cookie", 4)

    assert error.value.code == "insufficient_stock"
    assert error.value.details["item_code"] == "cookie"
    assert error.value.details["requested"] == 4
    assert error.value.details["available"] == 3
    assert (await get_cart(db_session, car_a, tax_rate=TAX)).lines == []
    assert await holds(db_session, car_a) == []


async def test_the_refusal_offers_in_stock_items_from_the_same_category(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 0)
    await set_on_hand(db_session, "apple_pie", 0)

    with pytest.raises(InsufficientStock) as error:
        await add(db_session, car_a, "cookie")

    alternatives = error.value.details["alternatives"]
    # Other desserts, in menu order, without the apple pie that is also gone.
    assert [a["item_code"] for a in alternatives] == ["sundae", "soft_serve_cone"]
    assert alternatives[0]["item_name"] == "Sundae"
    assert "none are left" in error.value.message


async def test_exactly_the_last_units_can_be_taken(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)

    cart = await add(db_session, car_a, "cookie", 3)

    assert cart.item_count == 3
    assert (await level(db_session, "cookie")).available == 0


async def test_several_lines_of_one_item_share_a_single_hold(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)

    await add(db_session, car_a, "cookie", 1)
    await add(db_session, car_a, "cookie", 2)

    (hold,) = await holds(db_session, car_a)
    assert hold.quantity == 3
    with pytest.raises(InsufficientStock):
        await add(db_session, car_a, "cookie", 1)


async def test_an_item_whose_stock_is_not_tracked_is_unlimited(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    cookie_id = (
        await db_session.execute(select(MenuItem.id).where(MenuItem.code == "cookie"))
    ).scalar_one()
    await db_session.execute(delete(Inventory).where(Inventory.item_id == cookie_id))

    cart = await add(db_session, car_a, "cookie", 500)

    assert cart.item_count == 500
    assert await holds(db_session, car_a) == []


# --- Two cars ---------------------------------------------------------------


async def test_one_car_cannot_take_what_another_is_holding(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)
    await add(db_session, car_a, "cookie", 2)

    with pytest.raises(InsufficientStock) as error:
        await add(db_session, car_b, "cookie", 2)
    cart = await add(db_session, car_b, "cookie", 1)

    assert error.value.details["available"] == 1
    assert cart.item_count == 1
    assert (await level(db_session, "cookie")).available == 0


async def test_stock_is_free_again_when_the_other_car_removes_the_line(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")

    await remove_line(db_session, car_a, 1, tax_rate=TAX)
    cart = await add(db_session, car_b, "cookie")

    assert cart.item_count == 1
    (released,) = await holds(db_session, car_a)
    assert released.status is ReservationStatus.RELEASED
    assert released.released_reason == "no_longer_needed"


async def test_stock_is_free_again_when_the_other_car_drives_off(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")

    await end_session(db_session, car_a, SessionState.ABANDONED, "silence_timeout")
    cart = await add(db_session, car_b, "cookie")

    assert cart.item_count == 1
    (released,) = await holds(db_session, car_a)
    assert released.released_reason == "abandoned"


async def test_a_handed_off_order_keeps_its_stock(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    """A person is about to finish that order; the stock is still spoken for."""
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")

    await gateway.hand_to_human(db_session, car_a, "customer asked", tax_rate=TAX)

    (hold,) = await holds(db_session, car_a)
    assert hold.status is ReservationStatus.ACTIVE
    with pytest.raises(InsufficientStock):
        await add(db_session, car_b, "cookie")


# --- Changing a quantity ----------------------------------------------------


async def test_raising_a_quantity_holds_more(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 5)
    await add(db_session, car_a, "cookie", 2)

    await update_line(db_session, car_a, 1, quantity=5, tax_rate=TAX)

    assert (await holds(db_session, car_a))[0].quantity == 5


async def test_raising_a_quantity_past_the_stock_is_refused_and_the_line_is_kept(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 5)
    await add(db_session, car_a, "cookie", 2)

    with pytest.raises(InsufficientStock) as error:
        await update_line(db_session, car_a, 1, quantity=6, tax_rate=TAX)

    assert error.value.details["requested"] == 6
    cart = await get_cart(db_session, car_a, tax_rate=TAX)
    assert cart.lines[0].quantity == 2
    assert cart.version == 1
    assert (await holds(db_session, car_a))[0].quantity == 2


async def test_lowering_a_quantity_gives_stock_back(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 5)
    await add(db_session, car_a, "cookie", 5)

    await update_line(db_session, car_a, 1, quantity=2, tax_rate=TAX)
    cart = await add(db_session, car_b, "cookie", 3)

    assert cart.item_count == 3


async def test_changing_only_options_does_not_touch_stock(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "fries", 1)
    await add_item(
        db_session, car_a, "fries", selections={"size": ["small"]}, tax_rate=TAX
    )

    cart = await update_line(
        db_session, car_a, 1, selections={"size": ["large"]}, tax_rate=TAX
    )

    assert cart.lines[0].unit_price_cents == 379
    assert (await holds(db_session, car_a))[0].quantity == 1


async def test_a_line_can_be_removed_even_after_stock_was_lowered_below_it(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    """Giving stock back must always be possible."""
    await set_on_hand(db_session, "cookie", 3)
    await add(db_session, car_a, "cookie", 2)
    await add(db_session, car_a, "cookie", 1)
    await set_on_hand(db_session, "cookie", 0)

    cart = await remove_line(db_session, car_a, 1, tax_rate=TAX)

    assert cart.item_count == 1


# --- Placing the order ------------------------------------------------------


async def test_submitting_takes_the_stock_for_good(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 3)
    await add(db_session, car_a, "cookie", 2)

    await submit_order(db_session, car_a, tax_rate=TAX)

    cookies = await level(db_session, "cookie")
    assert (cookies.on_hand, cookies.reserved, cookies.available) == (1, 0, 1)
    assert (await holds(db_session, car_a))[0].status is ReservationStatus.COMMITTED
    with pytest.raises(InsufficientStock):
        await add(db_session, car_b, "cookie", 2)


async def test_an_order_takes_stock_for_every_item_in_it(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 10)
    await set_on_hand(db_session, "apple_pie", 10)
    await add(db_session, car_a, "cookie", 2)
    await add(db_session, car_a, "apple_pie", 3)
    await add(db_session, car_a, "cookie", 1)

    await submit_order(db_session, car_a, tax_rate=TAX)

    assert (await level(db_session, "cookie")).on_hand == 7
    assert (await level(db_session, "apple_pie")).on_hand == 7


# --- Expiry -----------------------------------------------------------------


async def test_an_expired_hold_no_longer_blocks_other_cars(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    """Nothing has cleaned the hold up; it simply stops counting."""
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")
    await expire_holds(db_session, car_a)

    cart = await add(db_session, car_b, "cookie")

    assert cart.item_count == 1
    assert (await holds(db_session, car_a))[0].status is ReservationStatus.ACTIVE


async def test_a_car_that_went_quiet_cannot_submit_stock_someone_else_took(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")
    await expire_holds(db_session, car_a)
    await add(db_session, car_b, "cookie")

    with pytest.raises(InsufficientStock) as error:
        await submit_order(db_session, car_a, tax_rate=TAX)

    assert error.value.details["available"] == 0
    # Nothing was taken or written, and the session can still be changed.
    assert (await level(db_session, "cookie")).on_hand == 1
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 0
    cart = await get_cart(db_session, car_a, tax_rate=TAX)
    assert cart.state is SessionState.ORDERING


async def test_a_car_that_went_quiet_can_still_submit_if_the_stock_is_there(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")
    await expire_holds(db_session, car_a)

    order = await submit_order(db_session, car_a, tax_rate=TAX)

    assert order.item_count == 1
    assert (await level(db_session, "cookie")).on_hand == 0


async def test_activity_in_a_session_refreshes_all_its_holds(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await add(db_session, car_a, "cookie")
    await db_session.execute(
        update(Reservation).values(expires_at=func.now() + timedelta(seconds=30))
    )

    await add_item(
        db_session,
        car_a,
        "apple_pie",
        tax_rate=TAX,
        reservation_ttl=timedelta(minutes=10),
    )

    remaining = (
        await db_session.execute(select(func.min(Reservation.expires_at) - func.now()))
    ).scalar_one()
    assert remaining > timedelta(minutes=9)


async def test_reading_the_order_back_also_refreshes_holds(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await add(db_session, car_a, "cookie")
    await db_session.execute(
        update(Reservation).values(expires_at=func.now() + timedelta(seconds=30))
    )

    await request_confirmation(db_session, car_a, tax_rate=TAX)

    remaining = (
        await db_session.execute(select(Reservation.expires_at - func.now()))
    ).scalar_one()
    assert remaining > timedelta(minutes=9)


async def test_an_expired_hold_is_not_revived_by_later_activity(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    """Reviving it could put two cars on the same unit."""
    await set_on_hand(db_session, "cookie", 1)
    await add(db_session, car_a, "cookie")
    await expire_holds(db_session, car_a)
    await add(db_session, car_b, "cookie")

    await add(db_session, car_a, "apple_pie")

    live = (await level(db_session, "cookie")).reserved
    assert live == 1  # only car B's


async def test_the_sweeper_releases_only_expired_holds(
    db_session: AsyncSession, car_a: uuid.UUID, car_b: uuid.UUID
) -> None:
    await add(db_session, car_a, "cookie")
    await add(db_session, car_b, "cookie")
    await expire_holds(db_session, car_a)

    released = await release_expired(db_session)

    assert released == 1
    (expired,) = await holds(db_session, car_a)
    (live,) = await holds(db_session, car_b)
    assert (expired.status, expired.released_reason) == (
        ReservationStatus.RELEASED,
        "expired",
    )
    assert live.status is ReservationStatus.ACTIVE


# --- What the menu says -----------------------------------------------------


async def test_the_menu_marks_items_that_are_sold_out(
    db_session: AsyncSession,
) -> None:
    await set_on_hand(db_session, "cookie", 0)

    menu = await get_menu(db_session, Language.EN)
    search = await search_menu(db_session, "cookie", Language.EN)
    details = await get_item_details(db_session, "cookie", Language.EN)

    items = {item.item_code: item for c in menu.categories for item in c.items}
    assert items["cookie"].in_stock is False
    assert items["apple_pie"].in_stock is True
    assert search[0].in_stock is False
    assert details.in_stock is False


async def test_an_item_fully_held_by_one_car_shows_as_sold_out(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 2)
    await add(db_session, car_a, "cookie", 2)

    details = await get_item_details(db_session, "cookie", Language.EN)

    assert details.in_stock is False


# --- Through the policy gateway ---------------------------------------------


async def test_the_agent_is_told_what_is_left_and_what_to_offer(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await set_on_hand(db_session, "cookie", 1)
    limits = PolicyLimits(10, 25, 50)

    outcome = await gateway.execute(
        db_session,
        car_a,
        AddItem(item_code="cookie", quantity=3),
        tax_rate=TAX,
        limits=limits,
    )

    assert (outcome.decision, outcome.rule) == (Decision.REJECT, "insufficient_stock")
    assert outcome.error is not None
    assert outcome.error.details["available"] == 1
    assert len(outcome.error.details["alternatives"]) == 3
    assert outcome.cart.lines == []


# --- Seeding ----------------------------------------------------------------


async def test_the_seed_sets_stock_and_running_it_again_restocks(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    assert len(await stock_levels(db_session)) == 20
    assert (await level(db_session, "cookie")).on_hand == 100
    await set_on_hand(db_session, "cookie", 2)

    await seed_menu(db_session, load_menu(MENU_PATH))

    assert (await level(db_session, "cookie")).on_hand == 100


# --- What the database itself refuses ---------------------------------------


async def test_stock_cannot_be_recorded_below_zero(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_inventory_on_hand_non_negative"):
        await db_session.execute(text("UPDATE inventory SET on_hand = -1"))


async def test_a_hold_must_be_for_at_least_one_unit(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await add(db_session, car_a, "cookie")

    with pytest.raises(IntegrityError, match="ck_reservations_quantity_positive"):
        await db_session.execute(text("UPDATE reservations SET quantity = 0"))


async def test_a_session_cannot_have_two_live_holds_on_one_item(
    db_session: AsyncSession, car_a: uuid.UUID
) -> None:
    await add(db_session, car_a, "cookie")

    with pytest.raises(
        IntegrityError, match="uq_reservations_one_active_per_session_item"
    ):
        await db_session.execute(
            text(
                "INSERT INTO reservations (session_id, item_id, quantity, expires_at) "
                "SELECT session_id, item_id, 1, now() FROM reservations"
            )
        )
