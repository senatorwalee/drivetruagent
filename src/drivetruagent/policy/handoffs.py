"""Sessions handed to a person: recording them, showing them, closing them.

A handoff row says why a session was handed off and what staff then did.
Everything else a person needs is read from the session's own rows when the
package is asked for. See `handoff_package` for its shape.

Nothing here commits. The caller owns the transaction.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    AgentEvent,
    CustomerSession,
    Handoff,
    MenuItem,
    Order,
    Reservation,
)
from drivetruagent.enums import HandoffOutcome, HandoffStatus, ReservationStatus
from drivetruagent.ordering import cart as cart_rules
from drivetruagent.ordering import orders as order_rules
from drivetruagent.ordering.inventory import release_reservations
from drivetruagent.ordering.sessions import get_session
from drivetruagent.ordering.views import DEFAULT_LANGUAGE, CartSnapshot, localize
from drivetruagent.policy.errors import HandoffNotFound, HandoffResolved
from drivetruagent.policy.handoff_package import (
    HandoffPackage,
    HandoffReason,
    HandoffSummary,
    HeldStock,
    LoggedEvent,
    outstanding,
    reason_title,
    steps_of,
    transcript_of,
)

HANDOFF_CANCELLED = "handoff_cancelled"
DEFAULT_QUEUE_LENGTH = 50


async def open_handoff(
    db: AsyncSession,
    session_id: uuid.UUID,
    reason: str,
    details: Mapping[str, Any],
    action: Mapping[str, Any] | None = None,
) -> Handoff:
    """Record that a session has been handed to a person, and why.

    Called in the same transaction that ends the session, so a handed-off
    session and its handoff exist together or not at all.
    """
    handoff = Handoff(
        session_id=session_id,
        reason=reason,
        details=dict(details),
        action=dict(action) if action is not None else None,
    )
    db.add(handoff)
    await db.flush()
    return handoff


async def list_handoffs(
    db: AsyncSession,
    *,
    tax_rate: Decimal,
    status: HandoffStatus | None = None,
    session_id: uuid.UUID | None = None,
    limit: int = DEFAULT_QUEUE_LENGTH,
) -> list[HandoffSummary]:
    """The queue: the longest-waiting handoff first."""
    statement = select(Handoff).order_by(Handoff.created_at, Handoff.id).limit(limit)
    if status is not None:
        statement = statement.where(Handoff.status == status)
    if session_id is not None:
        statement = statement.where(Handoff.session_id == session_id)
    handoffs = (await db.execute(statement)).scalars().all()

    summaries: list[HandoffSummary] = []
    for handoff in handoffs:
        customer_session = await get_session(db, handoff.session_id)
        cart = await cart_rules.get_cart(db, handoff.session_id, tax_rate=tax_rate)
        summaries.append(_summary(handoff, customer_session, cart))
    return summaries


async def get_package(
    db: AsyncSession, handoff_id: uuid.UUID, *, tax_rate: Decimal, now: datetime
) -> HandoffPackage:
    """Everything the person taking over needs. Raises HandoffNotFound.

    Built from the session's rows as they are now. The session has ended, so
    its cart cannot change; its event log can only have grown.
    """
    handoff = await _load(db, handoff_id)
    session_id = handoff.session_id
    customer_session = await get_session(db, session_id)
    cart = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)
    events = await _logged_events(db, session_id)
    transcript, earlier_lines = transcript_of(events)
    steps, earlier_steps = steps_of(events)
    order_id = (
        await db.execute(select(Order.id).where(Order.session_id == session_id))
    ).scalar_one_or_none()

    return HandoffPackage(
        **_summary(handoff, customer_session, cart).model_dump(),
        details=handoff.details,
        outstanding=outstanding(
            handoff.reason,
            handoff.details,
            handoff.action,
            item_name=await _item_name(db, handoff.action),
            cart_item_count=cart.item_count,
        ),
        acknowledged_at=handoff.acknowledged_at,
        resolved_at=handoff.resolved_at,
        outcome=handoff.outcome,
        note=handoff.note,
        session_started_at=customer_session.started_at,
        session_ended_at=customer_session.ended_at,
        cart=cart,
        stock=await _held_stock(db, session_id, now),
        transcript=transcript,
        earlier_lines=earlier_lines,
        steps=steps,
        earlier_steps=earlier_steps,
        order=await order_rules.get_order(db, order_id) if order_id else None,
    )


async def acknowledge(
    db: AsyncSession, handoff_id: uuid.UUID, *, by: str, now: datetime
) -> Handoff:
    """Someone has picked this handoff up.

    Doing it again changes nothing and keeps the first person's name: the
    point is to know someone has it, and they do.
    """
    handoff = await _load(db, handoff_id, lock=True)
    _refuse_if_resolved(handoff)
    if handoff.status is HandoffStatus.OPEN:
        handoff.status = HandoffStatus.ACKNOWLEDGED
        handoff.acknowledged_at = now
        handoff.acknowledged_by = by
        await db.flush()
    return handoff


async def resolve(
    db: AsyncSession,
    handoff_id: uuid.UUID,
    outcome: HandoffOutcome,
    *,
    now: datetime,
    tax_rate: Decimal,
    note: str | None = None,
) -> Handoff:
    """Finish a handoff: place the order as it stands, or cancel it.

    Placing it can fail (the cart is empty, or the stock has gone). The
    error is raised before anything about the handoff is changed, so it
    stays open for the person to deal with.
    """
    # The handoff's lock makes two people closing it take turns. The second
    # finds it resolved and is told so.
    handoff = await _load(db, handoff_id, lock=True)
    _refuse_if_resolved(handoff)

    if outcome is HandoffOutcome.ORDER_PLACED:
        await order_rules.place_handed_off_order(
            db, handoff.session_id, tax_rate=tax_rate
        )
    else:
        # Nobody is coming back for this order, so its stock is free again.
        await release_reservations(db, handoff.session_id, reason=HANDOFF_CANCELLED)

    handoff.status = HandoffStatus.RESOLVED
    handoff.resolved_at = now
    handoff.outcome = outcome
    handoff.note = note
    await db.flush()
    return handoff


# --- Helpers ----------------------------------------------------------------


async def _load(
    db: AsyncSession, handoff_id: uuid.UUID, *, lock: bool = False
) -> Handoff:
    statement = (
        select(Handoff)
        .where(Handoff.id == handoff_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    handoff = (await db.execute(statement)).scalar_one_or_none()
    if handoff is None:
        raise HandoffNotFound(handoff_id)
    return handoff


def _refuse_if_resolved(handoff: Handoff) -> None:
    if handoff.status is HandoffStatus.RESOLVED and handoff.outcome is not None:
        raise HandoffResolved(handoff.outcome.value)


def _summary(
    handoff: Handoff, customer_session: CustomerSession, cart: CartSnapshot
) -> HandoffSummary:
    return HandoffSummary(
        handoff_id=handoff.id,
        session_id=handoff.session_id,
        lane=customer_session.lane,
        language=customer_session.language or DEFAULT_LANGUAGE,
        status=handoff.status,
        reason=HandoffReason(code=handoff.reason, title=reason_title(handoff.reason)),
        created_at=handoff.created_at,
        acknowledged_by=handoff.acknowledged_by,
        item_count=cart.item_count,
        total_cents=cart.total_cents,
    )


async def _logged_events(db: AsyncSession, session_id: uuid.UUID) -> list[LoggedEvent]:
    rows = await db.execute(
        select(AgentEvent.kind, AgentEvent.payload, AgentEvent.created_at)
        .where(AgentEvent.session_id == session_id)
        .order_by(AgentEvent.seq)
    )
    return [LoggedEvent(kind, payload, at) for kind, payload, at in rows]


async def _item_name(db: AsyncSession, action: Mapping[str, Any] | None) -> str | None:
    """The name of the item the triggering action was about, for staff."""
    item_code = (action or {}).get("item_code")
    if not item_code:
        return None
    name = (
        await db.execute(select(MenuItem.name_i18n).where(MenuItem.code == item_code))
    ).scalar_one_or_none()
    return localize(name, DEFAULT_LANGUAGE) if name else str(item_code)


async def _held_stock(
    db: AsyncSession, session_id: uuid.UUID, now: datetime
) -> list[HeldStock]:
    """What the session's cart was holding, and whether the hold still counts."""
    rows = await db.execute(
        select(Reservation, MenuItem.code, MenuItem.name_i18n)
        .join(MenuItem, MenuItem.id == Reservation.item_id)
        .where(Reservation.session_id == session_id)
        .order_by(MenuItem.code, Reservation.created_at)
    )
    return [
        HeldStock(
            item_code=code,
            item_name=localize(name, DEFAULT_LANGUAGE),
            quantity=reservation.quantity,
            status=reservation.status,
            expires_at=reservation.expires_at,
            held=reservation.status is ReservationStatus.ACTIVE
            and reservation.expires_at > now,
        )
        for reservation, code, name in rows
    ]
