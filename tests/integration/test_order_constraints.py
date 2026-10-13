"""The database must refuse orders whose numbers do not add up.

Each rejection test names the constraint it expects, so a test cannot pass
because some other rule happened to fire.
"""

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    CustomerSession,
    MenuItem,
    Order,
    OrderLine,
    OrderLineOption,
)
from drivetruagent.enums import Language
from tests.integration.factories import (
    make_item,
    make_order,
    make_order_line,
    make_session,
)


async def count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


def frozen_option(line: OrderLine, **overrides: object) -> OrderLineOption:
    values: dict[str, object] = {
        "order_line_id": line.id,
        "position": 0,
        "group_code": "size",
        "group_name": "Size",
        "option_code": "large",
        "option_name": "Large",
        "price_delta_cents": 130,
    }
    return OrderLineOption(**values | overrides)


# --- Orders -----------------------------------------------------------------


async def test_order_gets_a_number_and_a_submission_time(
    db_session: AsyncSession,
) -> None:
    order = await make_order(db_session)

    assert order.order_number >= 1
    assert order.submitted_at is not None
    assert order.language is Language.EN


async def test_order_numbers_increase(db_session: AsyncSession) -> None:
    first = await make_order(db_session)
    second = await make_order(db_session)
    third = await make_order(db_session)

    assert first.order_number < second.order_number < third.order_number


async def test_order_number_cannot_be_chosen_by_the_caller(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)

    # GENERATED ALWAYS: Postgres refuses an explicit value.
    with pytest.raises(DBAPIError, match="order_number"):
        await db_session.execute(
            text(
                "INSERT INTO orders (order_number, session_id, language, "
                "subtotal_cents, tax_cents, total_cents) "
                "VALUES (7, :session_id, 'en', 100, 15, 115)"
            ),
            {"session_id": customer_session.id},
        )


async def test_second_order_for_one_session_is_rejected(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)
    await make_order(db_session, customer_session)

    with pytest.raises(IntegrityError, match="uq_orders_session_id"):
        await make_order(db_session, customer_session)


async def test_total_that_is_not_subtotal_plus_tax_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_orders_total_matches"):
        await make_order(
            db_session, subtotal_cents=1000, tax_cents=150, total_cents=1000
        )


async def test_negative_subtotal_is_rejected(db_session: AsyncSession) -> None:
    # Two rules refuse this row: the subtotal is negative, and the discount
    # (zero) is larger than it. Postgres reports whichever it checks first.
    refused_by = "ck_orders_(subtotal_cents_non_negative|discount_within_subtotal)"
    with pytest.raises(IntegrityError, match=refused_by):
        await make_order(db_session, subtotal_cents=-100, tax_cents=100, total_cents=0)


async def test_an_order_with_a_discount_must_still_add_up(
    db_session: AsyncSession,
) -> None:
    order = await make_order(
        db_session,
        subtotal_cents=1000,
        discount_cents=200,
        tax_cents=104,
        total_cents=904,
    )

    assert order.discount_cents == 200
    with pytest.raises(IntegrityError, match="ck_orders_total_matches"):
        await make_order(
            db_session,
            subtotal_cents=1000,
            discount_cents=200,
            tax_cents=104,
            total_cents=1104,
        )


async def test_an_order_has_no_discount_unless_one_is_given(
    db_session: AsyncSession,
) -> None:
    assert (await make_order(db_session)).discount_cents == 0


async def test_negative_discount_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_orders_discount_cents_non_negative"):
        await make_order(
            db_session,
            subtotal_cents=1000,
            discount_cents=-100,
            tax_cents=0,
            total_cents=1100,
        )


async def test_a_discount_larger_than_the_subtotal_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_orders_discount_within_subtotal"):
        await make_order(
            db_session,
            subtotal_cents=100,
            discount_cents=200,
            tax_cents=100,
            total_cents=0,
        )


async def test_negative_tax_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_orders_tax_cents_non_negative"):
        await make_order(db_session, subtotal_cents=100, tax_cents=-10, total_cents=90)


async def test_order_in_an_unknown_language_is_rejected(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)

    with pytest.raises(IntegrityError, match="ck_orders_language_valid"):
        await db_session.execute(
            text(
                "INSERT INTO orders (session_id, language, subtotal_cents, "
                "tax_cents, total_cents) VALUES (:session_id, 'de', 100, 15, 115)"
            ),
            {"session_id": customer_session.id},
        )


async def test_session_with_an_order_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)
    await make_order(db_session, customer_session)

    with pytest.raises(IntegrityError, match="fk_orders_session_id_sessions"):
        await db_session.execute(
            delete(CustomerSession).where(CustomerSession.id == customer_session.id)
        )


# --- Order lines ------------------------------------------------------------


async def test_line_total_that_is_not_price_times_quantity_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_order_lines_line_total_matches"):
        await make_order_line(
            db_session, quantity=2, unit_price_cents=249, line_total_cents=249
        )


async def test_order_line_with_quantity_0_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_order_lines_quantity_positive"):
        await make_order_line(
            db_session, quantity=0, unit_price_cents=249, line_total_cents=0
        )


async def test_negative_unit_price_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(
        IntegrityError, match="ck_order_lines_unit_price_cents_non_negative"
    ):
        await make_order_line(
            db_session, quantity=1, unit_price_cents=-5, line_total_cents=-5
        )


async def test_two_lines_with_the_same_number_in_one_order_are_rejected(
    db_session: AsyncSession,
) -> None:
    order = await make_order(db_session)
    await make_order_line(db_session, order, line_no=1)

    with pytest.raises(IntegrityError, match="uq_order_lines_order_id_line_no"):
        await make_order_line(db_session, order, line_no=1)


async def test_menu_item_on_an_order_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    await make_order_line(db_session, item=item)

    with pytest.raises(IntegrityError, match="fk_order_lines_item_id_menu_items"):
        await db_session.execute(delete(MenuItem).where(MenuItem.id == item.id))


async def test_order_keeps_its_frozen_name_when_the_menu_changes(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    line = await make_order_line(db_session, item=item, item_name="Fries")
    line_id = line.id

    await db_session.execute(
        update(MenuItem)
        .where(MenuItem.id == item.id)
        .values(name_i18n={"en": "Chips", "fr": "Croustilles"}, base_price_cents=999)
    )
    db_session.expire_all()

    loaded = await db_session.get_one(OrderLine, line_id)
    assert loaded.item_name == "Fries"
    assert loaded.unit_price_cents == 249


# --- Frozen options ---------------------------------------------------------


async def test_same_frozen_option_twice_on_one_line_is_rejected(
    db_session: AsyncSession,
) -> None:
    line = await make_order_line(db_session)
    db_session.add(frozen_option(line))
    await db_session.flush()
    db_session.add(frozen_option(line))

    with pytest.raises(
        IntegrityError,
        match="uq_order_line_options_order_line_id_group_code_option_code",
    ):
        await db_session.flush()


async def test_negative_frozen_price_delta_is_rejected(
    db_session: AsyncSession,
) -> None:
    line = await make_order_line(db_session)
    db_session.add(frozen_option(line, price_delta_cents=-1))

    with pytest.raises(
        IntegrityError, match="ck_order_line_options_price_delta_cents_non_negative"
    ):
        await db_session.flush()


async def test_negative_option_position_is_rejected(
    db_session: AsyncSession,
) -> None:
    line = await make_order_line(db_session)
    db_session.add(frozen_option(line, position=-1))

    with pytest.raises(
        IntegrityError, match="ck_order_line_options_position_non_negative"
    ):
        await db_session.flush()


# --- Cascades ---------------------------------------------------------------


async def test_deleting_an_order_removes_its_lines_and_options(
    db_session: AsyncSession,
) -> None:
    order = await make_order(db_session)
    line = await make_order_line(db_session, order)
    db_session.add(frozen_option(line))
    await db_session.flush()

    await db_session.execute(delete(Order).where(Order.id == order.id))

    assert await count(db_session, OrderLine) == 0
    assert await count(db_session, OrderLineOption) == 0
