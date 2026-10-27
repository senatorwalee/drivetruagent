"""Remembering who called and what they ordered.

A caller is a phone number. Their orders are the orders of the sessions that
link to it, read from the frozen order rows: what was ordered, at the price
then. Nothing is copied, so there is no second record to keep in step.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import Caller, CustomerSession, Order, OrderLine
from drivetruagent.enums import ACTIVE_SESSION_STATES, Channel

# How many orders a caller can be reminded of.
PAST_ORDERS_LIMIT = 5


async def record_call(db: AsyncSession, phone_e164: str) -> uuid.UUID:
    """Count a call from this number, creating the caller on its first call.

    One statement, so two calls from the same number at the same moment both
    count and neither fails.
    """
    statement = (
        insert(Caller)
        .values(phone_e164=phone_e164)
        .on_conflict_do_update(
            index_elements=[Caller.phone_e164],
            set_={
                "call_count": Caller.call_count + 1,
                "last_call_at": func.now(),
                "updated_at": func.now(),
            },
        )
        .returning(Caller.id)
    )
    return (await db.execute(statement)).scalar_one()


class PastOption(BaseModel):
    group_code: str
    option_code: str
    option_name: str


class PastLine(BaseModel):
    item_code: str
    item_name: str
    quantity: int
    options: list[PastOption]


class PastOrder(BaseModel):
    order_number: int
    # The restaurant's date when it was ordered.
    ordered_on: date
    total_cents: int
    lines: list[PastLine]


async def past_orders(
    db: AsyncSession, session_id: uuid.UUID, *, limit: int = PAST_ORDERS_LIMIT
) -> list[PastOrder]:
    """The session caller's latest orders, newest first.

    Empty when there is no caller (a drive-through car, or a withheld number)
    and when the caller asked for their orders not to be kept: in both cases
    there is nothing that may be read back.
    """
    caller = await _caller_of(db, session_id)
    if caller is None or caller.history_opted_out:
        return []
    rows = (
        await db.execute(
            select(Order, CustomerSession.business_date)
            .join(CustomerSession, Order.session_id == CustomerSession.id)
            .where(CustomerSession.caller_id == caller.id)
            .order_by(Order.submitted_at.desc(), Order.order_number.desc())
            .limit(limit)
            .options(selectinload(Order.lines).selectinload(OrderLine.options))
        )
    ).all()
    return [
        PastOrder(
            order_number=order.order_number,
            ordered_on=business_date,
            total_cents=order.total_cents,
            lines=[
                PastLine(
                    item_code=line.item_code,
                    item_name=line.item_name,
                    quantity=line.quantity,
                    options=[
                        PastOption(
                            group_code=option.group_code,
                            option_code=option.option_code,
                            option_name=option.option_name,
                        )
                        for option in line.options
                    ],
                )
                for line in order.lines
            ],
        )
        for order, business_date in rows
    ]


class ActiveCall(BaseModel):
    session_id: uuid.UUID
    started_at: datetime
    # The last four digits, for telling calls apart on a screen; None if the
    # number was withheld. The whole number is not needed there.
    caller_last_four: str | None


async def active_calls(db: AsyncSession, *, limit: int = 10) -> list[ActiveCall]:
    """Phone calls in progress, newest first."""
    rows = (
        await db.execute(
            select(CustomerSession.id, CustomerSession.started_at, Caller.phone_e164)
            .outerjoin(Caller, CustomerSession.caller_id == Caller.id)
            .where(
                CustomerSession.channel == Channel.PHONE,
                CustomerSession.state.in_(ACTIVE_SESSION_STATES),
            )
            .order_by(CustomerSession.started_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        ActiveCall(
            session_id=session_id,
            started_at=started_at,
            caller_last_four=None if phone is None else phone[-4:],
        )
        for session_id, started_at, phone in rows
    ]


async def forget_history(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Stop reading the session caller's orders back to them, from now on.

    Returns False if the session has no caller, so there was nothing to stop.
    """
    caller = await _caller_of(db, session_id)
    if caller is None:
        return False
    await db.execute(
        update(Caller)
        .where(Caller.id == caller.id)
        .values(history_opted_out=True, updated_at=func.now())
    )
    return True


async def _caller_of(db: AsyncSession, session_id: uuid.UUID) -> Caller | None:
    return (
        await db.execute(
            select(Caller)
            .join(CustomerSession, CustomerSession.caller_id == Caller.id)
            .where(CustomerSession.id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
