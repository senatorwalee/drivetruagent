"""Turning a cart into an order."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import Order, OrderLine, OrderLineOption
from drivetruagent.enums import SessionState
from drivetruagent.ordering.cart import (
    load_cart,
    load_priced_lines,
    snapshot_from_lines,
)
from drivetruagent.ordering.errors import EmptyCart, OrderNotFound
from drivetruagent.ordering.sessions import lock_active_session
from drivetruagent.ordering.views import LineView, OrderView, SelectedOptionView

ORDER_SUBMITTED = "order_submitted"


async def submit_order(
    db: AsyncSession, session_id: uuid.UUID, *, tax_rate: Decimal
) -> OrderView:
    """Freeze the cart into an order and end the session as submitted.

    Names and prices are copied into the order rows. From here on the order is
    history: nothing that later happens to the menu can change it.

    Does not commit. The order and the session's new state become permanent
    together, when the caller commits.
    """
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)
    lines = await load_priced_lines(db, cart)
    if not lines:
        raise EmptyCart()
    snapshot = snapshot_from_lines(customer_session, cart, lines, tax_rate)

    order = Order(
        session_id=session_id,
        language=snapshot.language,
        subtotal_cents=snapshot.subtotal_cents,
        tax_cents=snapshot.tax_cents,
        total_cents=snapshot.total_cents,
    )
    db.add(order)
    await db.flush()

    for line, view in zip(lines, snapshot.lines, strict=True):
        order_line = OrderLine(
            order_id=order.id,
            line_no=view.line_no,
            item_id=line.item.id,
            item_code=view.item_code,
            item_name=view.item_name,
            quantity=view.quantity,
            unit_price_cents=view.unit_price_cents,
            line_total_cents=view.line_total_cents,
        )
        db.add(order_line)
        await db.flush()
        db.add_all(
            OrderLineOption(
                order_line_id=order_line.id,
                position=position,
                group_code=option.group_code,
                group_name=option.group_name,
                option_code=option.option_code,
                option_name=option.option_name,
                price_delta_cents=option.price_delta_cents,
            )
            for position, option in enumerate(view.options)
        )

    customer_session.state = SessionState.SUBMITTED
    customer_session.ended_at = datetime.now(UTC)
    customer_session.end_reason = ORDER_SUBMITTED
    await db.flush()

    return OrderView(
        order_id=order.id,
        order_number=order.order_number,
        session_id=session_id,
        language=snapshot.language,
        lines=snapshot.lines,
        item_count=snapshot.item_count,
        subtotal_cents=snapshot.subtotal_cents,
        tax_cents=snapshot.tax_cents,
        total_cents=snapshot.total_cents,
        submitted_at=order.submitted_at,
    )


async def get_order(db: AsyncSession, order_id: uuid.UUID) -> OrderView:
    """Read a submitted order back, exactly as it was frozen.

    Built only from the order's own rows. The menu is not consulted, so the
    result is the same however the menu has changed since.
    """
    statement = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.lines).selectinload(OrderLine.options))
        .execution_options(populate_existing=True)
    )
    order = (await db.execute(statement)).scalar_one_or_none()
    if order is None:
        raise OrderNotFound(order_id)

    lines = [
        LineView(
            line_no=line.line_no,
            item_code=line.item_code,
            item_name=line.item_name,
            quantity=line.quantity,
            options=[
                SelectedOptionView(
                    group_code=option.group_code,
                    group_name=option.group_name,
                    option_code=option.option_code,
                    option_name=option.option_name,
                    price_delta_cents=option.price_delta_cents,
                )
                for option in line.options
            ],
            unit_price_cents=line.unit_price_cents,
            line_total_cents=line.line_total_cents,
        )
        for line in order.lines
    ]
    return OrderView(
        order_id=order.id,
        order_number=order.order_number,
        session_id=order.session_id,
        language=order.language,
        lines=lines,
        item_count=sum(line.quantity for line in lines),
        subtotal_cents=order.subtotal_cents,
        tax_cents=order.tax_cents,
        total_cents=order.total_cents,
        submitted_at=order.submitted_at,
    )
