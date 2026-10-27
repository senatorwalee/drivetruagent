"""Session, cart and order endpoints.

No endpoint holds a rule. Each one calls an ordering function, commits, and
returns the result. If the function raises, nothing is committed: the error
handler answers, and closing the request's database session discards any
partial work.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status

from drivetruagent.api.deps import (
    BusinessDateDep,
    IdempotencyKey,
    SessionDep,
    SettingsDep,
)
from drivetruagent.api.errors import CONFLICT, FORBIDDEN, NOT_FOUND, REFUSED
from drivetruagent.api.schemas import (
    AddLineRequest,
    OpenSessionRequest,
    UpdateLineRequest,
    UpdateSessionRequest,
)
from drivetruagent.config import Settings
from drivetruagent.db.models import CustomerSession
from drivetruagent.ordering import cart, orders, sessions
from drivetruagent.ordering.errors import InvalidPhoneNumber, TypedCallerIdOff
from drivetruagent.ordering.idempotency import run_once
from drivetruagent.ordering.phone_numbers import normalize_phone
from drivetruagent.ordering.views import CartSnapshot, OrderView, SessionView

router = APIRouter(tags=["ordering"])


def _view(customer_session: CustomerSession, settings: Settings) -> SessionView:
    return sessions.to_view(
        customer_session, now=datetime.now(UTC), timers=settings.session_timers
    )


# --- Sessions ---------------------------------------------------------------


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT | FORBIDDEN,
    summary="Open a session",
)
async def open_session(
    db: SessionDep,
    today: BusinessDateDep,
    settings: SettingsDep,
    body: OpenSessionRequest | None = None,
) -> SessionView:
    """Start a customer's visit, with an empty cart. Refused if the lane
    already has a session in progress.

    The session keeps today's date, where the restaurant is, for as long as
    it lasts: a special heard about before midnight is still given after it."""
    request = body or OpenSessionRequest()
    caller_phone = None
    if request.caller_phone is not None:
        if not settings.allow_typed_caller_id:
            raise TypedCallerIdOff()
        caller_phone = normalize_phone(request.caller_phone)
        if caller_phone is None:
            raise InvalidPhoneNumber()
    customer_session = await sessions.open_session(
        db,
        request.lane,
        business_date=today,
        channel=request.channel,
        caller_phone=caller_phone,
    )
    await db.commit()
    return _view(customer_session, settings)


@router.get("/sessions/{session_id}", responses=NOT_FOUND, summary="Read a session")
async def read_session(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> SessionView:
    """The session, with when its timers will next fire. A screen counts
    down to those times; it never works the timer rules out itself."""
    return _view(await sessions.get_session(db, session_id), settings)


@router.patch(
    "/sessions/{session_id}",
    responses=NOT_FOUND | CONFLICT,
    summary="Set the session's language",
)
async def update_session(
    session_id: uuid.UUID,
    body: UpdateSessionRequest,
    db: SessionDep,
    settings: SettingsDep,
) -> SessionView:
    """Names in later cart snapshots, and in the order, follow this language."""
    customer_session = await sessions.set_language(db, session_id, body.language)
    await db.commit()
    return _view(customer_session, settings)


@router.post(
    "/sessions/{session_id}/pause",
    responses=NOT_FOUND | CONFLICT,
    summary="Stop the session's clocks",
)
async def pause_session(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> SessionView:
    """For a presenter who wants to talk without the session timing out.

    While paused, no timer fires and the time does not count toward the
    session's length. Pausing twice is the same as pausing once. The session
    also resumes by itself as soon as the customer says something.
    """
    customer_session = await sessions.pause(db, session_id, now=datetime.now(UTC))
    await db.commit()
    return _view(customer_session, settings)


@router.post(
    "/sessions/{session_id}/resume",
    responses=NOT_FOUND | CONFLICT,
    summary="Start the session's clocks again",
)
async def resume_session(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> SessionView:
    """The customer gets a fresh stretch of silence before being asked
    whether they are still there."""
    customer_session = await sessions.resume(db, session_id, now=datetime.now(UTC))
    await db.commit()
    return _view(customer_session, settings)


# --- Cart -------------------------------------------------------------------


@router.get("/sessions/{session_id}/cart", responses=NOT_FOUND, summary="Read the cart")
async def read_cart(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> CartSnapshot:
    return await cart.get_cart(db, session_id, tax_rate=settings.tax_rate)


@router.post(
    "/sessions/{session_id}/cart/lines",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | CONFLICT | REFUSED,
    summary="Add an item",
)
async def add_line(
    session_id: uuid.UUID, body: AddLineRequest, db: SessionDep, settings: SettingsDep
) -> CartSnapshot:
    """Add an item as a new line.

    If the item needs a choice that was not given and has no default, the
    request is refused with `missing_required_options`, listing each missing
    choice and the options to offer.
    """
    snapshot = await cart.add_item(
        db,
        session_id,
        body.item_code,
        body.quantity,
        body.selections,
        tax_rate=settings.tax_rate,
        reservation_ttl=settings.reservation_ttl,
    )
    await db.commit()
    return snapshot


@router.patch(
    "/sessions/{session_id}/cart/lines/{line_no}",
    responses=NOT_FOUND | CONFLICT | REFUSED,
    summary="Change a line",
)
async def update_line(
    session_id: uuid.UUID,
    line_no: int,
    body: UpdateLineRequest,
    db: SessionDep,
    settings: SettingsDep,
) -> CartSnapshot:
    """Change a line's quantity, its options, or both. Options change group by
    group: a group that is not named keeps its current choice."""
    snapshot = await cart.update_line(
        db,
        session_id,
        line_no,
        body.quantity,
        body.selections,
        tax_rate=settings.tax_rate,
        reservation_ttl=settings.reservation_ttl,
    )
    await db.commit()
    return snapshot


@router.delete(
    "/sessions/{session_id}/cart/lines/{line_no}",
    responses=NOT_FOUND | CONFLICT,
    summary="Remove a line",
)
async def remove_line(
    session_id: uuid.UUID, line_no: int, db: SessionDep, settings: SettingsDep
) -> CartSnapshot:
    snapshot = await cart.remove_line(
        db,
        session_id,
        line_no,
        tax_rate=settings.tax_rate,
        reservation_ttl=settings.reservation_ttl,
    )
    await db.commit()
    return snapshot


# --- Orders -----------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/order",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | CONFLICT,
    summary="Submit the cart as an order",
)
async def submit_order(
    session_id: uuid.UUID,
    db: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> OrderView:
    """Freeze the cart into an order and end the session.

    Send an `Idempotency-Key` header if the request might be retried: a repeat
    with the same key returns the same order instead of an error.
    """
    order = await run_once(
        db,
        session_id,
        idempotency_key,
        operation="submit_order",
        request={},
        response_model=OrderView,
        run=lambda: orders.submit_order(db, session_id, tax_rate=settings.tax_rate),
    )
    await db.commit()
    return order


@router.get("/orders/{order_id}", responses=NOT_FOUND, summary="Read an order")
async def read_order(order_id: uuid.UUID, db: SessionDep) -> OrderView:
    return await orders.get_order(db, order_id)
