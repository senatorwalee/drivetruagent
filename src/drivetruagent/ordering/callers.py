"""Remembering who called and what they ordered.

A caller is a phone number. Their orders are the orders of the sessions that
link to it, read from the frozen order rows: what was ordered, at the price
then. Nothing is copied, so there is no second record to keep in step.
"""

import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    delete,
    exists,
    func,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import (
    Caller,
    CallerStatusChange,
    CustomerSession,
    Order,
    OrderLine,
)
from drivetruagent.enums import (
    ACTIVE_SESSION_STATES,
    CallerStatus,
    Channel,
    PickupStatus,
)
from drivetruagent.ordering.errors import CallerNotFound

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


class CallerSummary(BaseModel):
    """A number, what it has done, and what staff decided about it.

    The counts are what the standing rules need (policy/caller_rules.py);
    what they add up to is decided there, not here.
    """

    caller_id: uuid.UUID
    phone_e164: str
    first_call_at: datetime
    last_call_at: datetime
    call_count: int
    history_opted_out: bool
    order_count: int
    # No-shows marked on orders placed within the no-show window.
    recent_no_shows: int
    # Calls within the repeat-call window that did not end in an order.
    recent_calls_without_order: int
    status: CallerStatus
    # The latest status change: why, by whom, when. None if never changed.
    status_reason: str | None
    status_changed_by: str | None
    status_changed_at: datetime | None


async def caller_summaries(
    db: AsyncSession,
    *,
    now: datetime,
    no_show_window: timedelta,
    repeat_call_window: timedelta,
    caller_id: uuid.UUID | None = None,
    phone_e164: str | None = None,
    limit: int = 200,
) -> list[CallerSummary]:
    """Callers, the most recent call first: every one, or the one with this
    id or number. Counted in one query, however many callers there are."""

    def count_orders(*conditions: ColumnElement[bool]) -> ScalarSelect[int]:
        return (
            select(func.count(Order.id))
            .join(CustomerSession, Order.session_id == CustomerSession.id)
            .where(CustomerSession.caller_id == Caller.id, *conditions)
            .scalar_subquery()
        )

    order_count = count_orders()
    recent_no_shows = count_orders(
        Order.pickup_status == PickupStatus.NO_SHOW,
        Order.submitted_at > now - no_show_window,
    )
    calls_without_order = (
        select(func.count())
        .select_from(CustomerSession)
        .where(
            CustomerSession.caller_id == Caller.id,
            CustomerSession.started_at > now - repeat_call_window,
            ~exists().where(Order.session_id == CustomerSession.id),
        )
        .scalar_subquery()
    )
    latest_change = (
        select(
            CallerStatusChange.reason,
            CallerStatusChange.changed_by,
            CallerStatusChange.changed_at,
        )
        .where(CallerStatusChange.caller_id == Caller.id)
        .order_by(CallerStatusChange.changed_at.desc())
        .limit(1)
        .lateral()
    )
    filters: list[ColumnElement[bool]] = []
    if caller_id is not None:
        filters.append(Caller.id == caller_id)
    if phone_e164 is not None:
        filters.append(Caller.phone_e164 == phone_e164)
    rows = (
        await db.execute(
            select(
                Caller,
                order_count,
                recent_no_shows,
                calls_without_order,
                latest_change.c.reason,
                latest_change.c.changed_by,
                latest_change.c.changed_at,
            )
            .outerjoin(latest_change, true())
            .where(*filters)
            .order_by(Caller.last_call_at.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
    ).all()
    return [
        CallerSummary(
            caller_id=caller.id,
            phone_e164=caller.phone_e164,
            first_call_at=caller.first_call_at,
            last_call_at=caller.last_call_at,
            call_count=caller.call_count,
            history_opted_out=caller.history_opted_out,
            order_count=orders,
            recent_no_shows=no_shows,
            recent_calls_without_order=without_order,
            status=caller.status,
            status_reason=reason,
            status_changed_by=changed_by,
            status_changed_at=changed_at,
        )
        for (
            caller,
            orders,
            no_shows,
            without_order,
            reason,
            changed_by,
            changed_at,
        ) in rows
    ]


async def caller_id_of(db: AsyncSession, session_id: uuid.UUID) -> uuid.UUID | None:
    """The session's caller, or None: a drive-through car or a withheld number."""
    caller = await _caller_of(db, session_id)
    return None if caller is None else caller.id


async def set_status(
    db: AsyncSession,
    caller_id: uuid.UUID,
    status: CallerStatus,
    *,
    reason: str,
    by: str,
    now: datetime,
) -> bool:
    """Flag, block or clear a number, and record who did it and why.

    Returns False, writing nothing, if the number already has that status.
    Raises CallerNotFound.
    """
    caller = (
        await db.execute(
            select(Caller)
            .where(Caller.id == caller_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if caller is None:
        raise CallerNotFound(caller_id)
    if caller.status is status:
        return False
    db.add(
        CallerStatusChange(
            caller_id=caller_id,
            from_status=caller.status,
            to_status=status,
            reason=reason,
            changed_by=by,
            changed_at=now,
        )
    )
    caller.status = status
    await db.flush()
    return True


async def delete_caller(db: AsyncSession, caller_id: uuid.UUID) -> str:
    """Forget a number entirely, as a caller may ask. Their orders stay, no
    longer linked to anyone, so the day's takings still add up. Returns the
    number's last four digits, for the record of who deleted what.

    Raises CallerNotFound.
    """
    deleted = (
        await db.execute(
            delete(Caller).where(Caller.id == caller_id).returning(Caller.phone_e164)
        )
    ).scalar_one_or_none()
    if deleted is None:
        raise CallerNotFound(caller_id)
    return deleted[-4:]


async def forget_inactive(db: AsyncSession, *, called_before: datetime) -> int:
    """Delete every caller whose last call was before this moment. Returns
    how many were deleted."""
    result = await db.execute(
        delete(Caller).where(Caller.last_call_at < called_before).returning(Caller.id)
    )
    return len(result.all())


async def _caller_of(db: AsyncSession, session_id: uuid.UUID) -> Caller | None:
    return (
        await db.execute(
            select(Caller)
            .join(CustomerSession, CustomerSession.caller_id == Caller.id)
            .where(CustomerSession.id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
