"""Session, cart and order endpoints.

No endpoint holds a rule. Each one calls an ordering function, commits, and
returns the result. If the function raises, nothing is committed: the error
handler answers, and closing the request's database session discards any
partial work.
"""

import uuid

from fastapi import APIRouter, status

from drivetruagent.api.deps import SessionDep, SettingsDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND, REFUSED
from drivetruagent.api.schemas import (
    AddLineRequest,
    OpenSessionRequest,
    UpdateLineRequest,
    UpdateSessionRequest,
)
from drivetruagent.ordering import cart, orders, sessions
from drivetruagent.ordering.views import CartSnapshot, OrderView, SessionView

router = APIRouter(tags=["ordering"])


# --- Sessions ---------------------------------------------------------------


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
    summary="Open a session",
)
async def open_session(
    db: SessionDep, body: OpenSessionRequest | None = None
) -> SessionView:
    """Start a customer's visit, with an empty cart. Refused if the lane
    already has a session in progress."""
    lane = (body or OpenSessionRequest()).lane
    customer_session = await sessions.open_session(db, lane)
    await db.commit()
    return SessionView.model_validate(customer_session)


@router.get("/sessions/{session_id}", responses=NOT_FOUND, summary="Read a session")
async def read_session(session_id: uuid.UUID, db: SessionDep) -> SessionView:
    return SessionView.model_validate(await sessions.get_session(db, session_id))


@router.patch(
    "/sessions/{session_id}",
    responses=NOT_FOUND | CONFLICT,
    summary="Set the session's language",
)
async def update_session(
    session_id: uuid.UUID, body: UpdateSessionRequest, db: SessionDep
) -> SessionView:
    """Names in later cart snapshots, and in the order, follow this language."""
    customer_session = await sessions.set_language(db, session_id, body.language)
    await db.commit()
    return SessionView.model_validate(customer_session)


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
        db, session_id, line_no, tax_rate=settings.tax_rate
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
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> OrderView:
    """Freeze the cart into an order and end the session."""
    order = await orders.submit_order(db, session_id, tax_rate=settings.tax_rate)
    await db.commit()
    return order


@router.get("/orders/{order_id}", responses=NOT_FOUND, summary="Read an order")
async def read_order(order_id: uuid.UUID, db: SessionDep) -> OrderView:
    return await orders.get_order(db, order_id)
