"""Phone orders waiting to be collected, and staff marking whether they were.

Nothing here decides anything about a caller. A no-show is only ever
recorded because a person said so; what no-shows mean for a number is
worked out in policy/caller_rules.py.
"""

import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import Caller, CustomerSession, Order
from drivetruagent.enums import PickupStatus
from drivetruagent.ordering.errors import NotAPickupOrder, OrderNotFound

# Marked pickups stay on the list this long, so a wrong mark can be put right.
RECENTLY_MARKED = timedelta(hours=12)


class PickupLine(BaseModel):
    quantity: int
    item_name: str


class Pickup(BaseModel):
    order_id: uuid.UUID
    order_number: int
    submitted_at: datetime
    total_cents: int
    lines: list[PickupLine]
    # The last four digits, to match the order to the person at the counter;
    # None if the number was withheld.
    caller_last_four: str | None
    status: PickupStatus
    marked_by: str | None
    marked_at: datetime | None


async def list_pickups(
    db: AsyncSession, *, now: datetime, recent: timedelta = RECENTLY_MARKED
) -> list[Pickup]:
    """Every phone order still waiting, oldest first, then the ones marked
    recently, newest mark first."""
    orders = (
        await db.execute(
            select(Order, Caller.phone_e164)
            .join(CustomerSession, Order.session_id == CustomerSession.id)
            .outerjoin(Caller, CustomerSession.caller_id == Caller.id)
            .where(
                or_(
                    Order.pickup_status == PickupStatus.WAITING,
                    Order.pickup_marked_at > now - recent,
                )
            )
            .options(selectinload(Order.lines))
            .execution_options(populate_existing=True)
        )
    ).all()
    pickups = [_pickup(order, phone) for order, phone in orders]
    waiting = sorted(
        (p for p in pickups if p.status is PickupStatus.WAITING),
        key=lambda p: (p.submitted_at, p.order_number),
    )
    marked = sorted(
        (p for p in pickups if p.status is not PickupStatus.WAITING),
        key=lambda p: (p.marked_at, p.order_number),
        reverse=True,
    )
    return waiting + marked


async def mark_pickup(
    db: AsyncSession,
    order_id: uuid.UUID,
    status: PickupStatus,
    *,
    by: str,
    now: datetime,
) -> Pickup:
    """Record that a phone order was collected, or was not.

    A mark can be changed, to put a mistake right: someone said to have not
    come may turn up late. Marking it the same way again keeps the first mark.
    Raises OrderNotFound, or NotAPickupOrder for a drive-through order.
    """
    if status is PickupStatus.WAITING:
        raise ValueError("A pickup is marked picked up or no-show, never waiting.")
    row = (
        await db.execute(
            select(Order, Caller.phone_e164)
            .join(CustomerSession, Order.session_id == CustomerSession.id)
            .outerjoin(Caller, CustomerSession.caller_id == Caller.id)
            .where(Order.id == order_id)
            .options(selectinload(Order.lines))
            .with_for_update(of=Order)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise OrderNotFound(order_id)
    order, phone = row
    if order.pickup_status is None:
        raise NotAPickupOrder(order_id)
    if order.pickup_status is not status:
        order.pickup_status = status
        order.pickup_marked_by = by
        order.pickup_marked_at = now
        await db.flush()
    return _pickup(order, phone)


def _pickup(order: Order, phone: str | None) -> Pickup:
    # Every order in these queries is a phone order, so it has a status.
    assert order.pickup_status is not None
    return Pickup(
        order_id=order.id,
        order_number=order.order_number,
        submitted_at=order.submitted_at,
        total_cents=order.total_cents,
        lines=[
            PickupLine(quantity=line.quantity, item_name=line.item_name)
            for line in order.lines
        ],
        caller_last_four=None if phone is None else phone[-4:],
        status=order.pickup_status,
        marked_by=order.pickup_marked_by,
        marked_at=order.pickup_marked_at,
    )
