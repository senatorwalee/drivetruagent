"""Turning a cart into an order, against a real database and the real menu."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import (
    CustomerSession,
    MenuItem,
    Option,
    Order,
    OrderLine,
)
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.cart import add_item, get_cart
from drivetruagent.ordering.errors import EmptyCart, SessionEnded, SessionNotFound
from drivetruagent.ordering.orders import submit_order
from drivetruagent.ordering.sessions import open_session

TAX = Decimal("0.14975")

pytestmark = pytest.mark.usefixtures("menu")


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    """An open session. Asking for `menu` here makes the menu load first."""
    return (await open_session(db_session)).id


@pytest.fixture
async def filled_cart(db_session: AsyncSession, session_id: uuid.UUID) -> None:
    """Two large fries and a medium cola: subtotal 997, tax 149, total 1146."""
    await add_item(
        db_session,
        session_id,
        "fries",
        quantity=2,
        selections={"size": ["large"]},
        tax_rate=TAX,
    )
    await add_item(
        db_session, session_id, "cola", selections={"size": ["medium"]}, tax_rate=TAX
    )


async def stored_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    statement = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.lines).selectinload(OrderLine.options))
        .execution_options(populate_existing=True)
    )
    return (await db.execute(statement)).scalar_one()


@pytest.mark.usefixtures("filled_cart")
async def test_order_matches_the_cart_it_came_from(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await get_cart(db_session, session_id, tax_rate=TAX)

    order = await submit_order(db_session, session_id, tax_rate=TAX)

    assert order.lines == cart.lines
    assert order.item_count == 3
    assert order.subtotal_cents == 997
    assert order.tax_cents == 149
    assert order.total_cents == 1146
    assert order.order_number >= 1
    assert order.session_id == session_id


@pytest.mark.usefixtures("filled_cart")
async def test_order_rows_hold_a_frozen_copy(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    view = await submit_order(db_session, session_id, tax_rate=TAX)

    order = await stored_order(db_session, view.order_id)
    fries, cola = order.lines
    assert (order.subtotal_cents, order.tax_cents, order.total_cents) == (
        997,
        149,
        1146,
    )
    assert (fries.line_no, fries.item_code, fries.item_name) == (1, "fries", "Fries")
    assert (fries.quantity, fries.unit_price_cents, fries.line_total_cents) == (
        2,
        379,
        758,
    )
    assert {
        (o.group_code, o.option_code, o.price_delta_cents) for o in fries.options
    } == {
        ("size", "large", 130),
        ("style", "regular", 0),
        ("seasoning", "plain", 0),
    }
    assert (cola.line_no, cola.item_code, cola.line_total_cents) == (2, "cola", 239)


@pytest.mark.usefixtures("filled_cart")
async def test_submitting_ends_the_session(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await submit_order(db_session, session_id, tax_rate=TAX)

    customer_session = await db_session.get_one(
        CustomerSession, session_id, populate_existing=True
    )
    assert customer_session.state is SessionState.SUBMITTED
    assert customer_session.ended_at is not None
    assert customer_session.end_reason == "order_submitted"


@pytest.mark.usefixtures("filled_cart")
async def test_lane_is_free_for_the_next_car_after_submission(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await submit_order(db_session, session_id, tax_rate=TAX)

    next_car = await open_session(db_session)

    assert next_car.id != session_id


@pytest.mark.usefixtures("filled_cart")
async def test_names_are_frozen_in_the_session_language(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.id == session_id)
        .values(language=Language.FR)
    )

    view = await submit_order(db_session, session_id, tax_rate=TAX)

    order = await stored_order(db_session, view.order_id)
    assert order.language is Language.FR
    assert order.lines[0].item_name == "Frites"
    assert "Grand" in {option.option_name for option in order.lines[0].options}


@pytest.mark.usefixtures("filled_cart")
async def test_later_menu_changes_do_not_alter_the_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    view = await submit_order(db_session, session_id, tax_rate=TAX)

    await db_session.execute(
        update(MenuItem)
        .where(MenuItem.code == "fries")
        .values(base_price_cents=999, name_i18n={"en": "Chips", "fr": "Croustilles"})
    )
    await db_session.execute(
        update(Option).where(Option.code == "large").values(price_delta_cents=500)
    )

    order = await stored_order(db_session, view.order_id)
    assert order.total_cents == 1146
    assert order.lines[0].item_name == "Fries"
    assert order.lines[0].unit_price_cents == 379


async def test_empty_cart_cannot_be_submitted(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(EmptyCart) as error:
        await submit_order(db_session, session_id, tax_rate=TAX)

    assert error.value.code == "empty_cart"
    customer_session = await db_session.get_one(
        CustomerSession, session_id, populate_existing=True
    )
    assert customer_session.state is SessionState.GREETING
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 0


@pytest.mark.usefixtures("filled_cart")
async def test_submitting_twice_creates_only_one_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await submit_order(db_session, session_id, tax_rate=TAX)

    with pytest.raises(SessionEnded) as error:
        await submit_order(db_session, session_id, tax_rate=TAX)

    assert error.value.details == {"state": "submitted"}
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 1


@pytest.mark.usefixtures("filled_cart")
async def test_cart_cannot_change_after_submission(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await submit_order(db_session, session_id, tax_rate=TAX)

    with pytest.raises(SessionEnded):
        await add_item(db_session, session_id, "cookie", tax_rate=TAX)


@pytest.mark.usefixtures("filled_cart")
async def test_cart_can_still_be_read_after_submission(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await submit_order(db_session, session_id, tax_rate=TAX)

    cart = await get_cart(db_session, session_id, tax_rate=TAX)

    assert cart.state is SessionState.SUBMITTED
    assert cart.total_cents == 1146


async def test_submitting_an_unknown_session_is_reported(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(SessionNotFound):
        await submit_order(db_session, uuid.uuid4(), tax_rate=TAX)


async def test_order_numbers_increase_across_sessions(
    db_session: AsyncSession,
) -> None:
    numbers = []
    for lane in ("lane-1", "lane-2", "lane-3"):
        session_id = (await open_session(db_session, lane)).id
        await add_item(db_session, session_id, "cookie", tax_rate=TAX)
        order = await submit_order(db_session, session_id, tax_rate=TAX)
        numbers.append(order.order_number)

    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 3
