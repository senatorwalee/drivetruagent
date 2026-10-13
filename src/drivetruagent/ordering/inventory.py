"""Stock: holding it for a session, giving it back, and taking it on submit.

The rule that prevents overselling is small and is applied in one place:

    lock the item's inventory row
    count what OTHER sessions hold in live reservations
    allow the change only if on_hand minus that count covers it

Because the row is locked first, two sessions asking for the last unit at the
same moment are decided one after the other. The first gets it; the second is
told there is none.

A reservation is live while it is active and has not expired. Expired ones
are ignored by the count itself, so nothing here depends on a cleanup job
having run. All times come from the database's clock.

Nothing here commits. The caller owns the transaction.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import Inventory, MenuItem, Reservation
from drivetruagent.enums import ReservationStatus
from drivetruagent.ordering.errors import InsufficientStock, UnknownItem

# How long stock stays held for a session with no activity.
DEFAULT_RESERVATION_TTL = timedelta(minutes=10)

NO_LONGER_NEEDED = "no_longer_needed"
EXPIRED = "expired"

# A reservation that still counts against what other sessions can have.
_LIVE = (
    Reservation.status == ReservationStatus.ACTIVE,
    Reservation.expires_at > func.now(),
)


@dataclass(frozen=True)
class StockNeed:
    """How many of an item a session needs in total, across its cart lines."""

    item_id: uuid.UUID
    item_code: str
    quantity: int


@dataclass(frozen=True)
class StockLevel:
    item_code: str
    on_hand: int
    # Held by live reservations, across all sessions.
    reserved: int

    @property
    def available(self) -> int:
        return max(0, self.on_hand - self.reserved)


# --- Holding and giving back ------------------------------------------------


async def reserve(
    db: AsyncSession,
    session_id: uuid.UUID,
    need: StockNeed,
    *,
    ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> None:
    """Set this session's hold on an item to exactly `need.quantity`.

    Raises InsufficientStock, having changed nothing, if other sessions' holds
    leave too little. A quantity of zero releases the hold. An item that is
    not stock-tracked is always allowed.
    """
    inventory = (await _lock_inventory(db, [need.item_id])).get(need.item_id)
    if inventory is None:
        return
    if need.quantity <= 0:
        await _release(db, session_id, [need.item_id], NO_LONGER_NEEDED)
        return

    # Asking for no more than is already held never fails, even if stock was
    # lowered in the meantime: giving some back must always be possible.
    already_held = await _held_by_session(db, need.item_id, session_id)
    if need.quantity > already_held:
        others = await _held_by_others(db, need.item_id, session_id)
        available = inventory.on_hand - others
        if need.quantity > available:
            raise InsufficientStock(need.item_code, need.quantity, max(0, available))

    # One live hold per session and item: insert it, or update the one there.
    await db.execute(
        insert(Reservation)
        .values(
            session_id=session_id,
            item_id=need.item_id,
            quantity=need.quantity,
            expires_at=func.now() + ttl,
        )
        .on_conflict_do_update(
            index_elements=["session_id", "item_id"],
            index_where=Reservation.status == ReservationStatus.ACTIVE,
            set_={
                "quantity": need.quantity,
                "expires_at": func.now() + ttl,
                "updated_at": func.now(),
            },
        )
    )


async def extend_reservations(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> None:
    """Give a session's holds a fresh expiry, because it has just been active.

    Only holds that are still live are extended. One that has already expired
    may have been taken by another session; reviving it here would let two
    sessions hold the same unit. It has to be asked for again through
    `reserve`, which checks.
    """
    await db.execute(
        update(Reservation)
        .where(Reservation.session_id == session_id, *_LIVE)
        .values(expires_at=func.now() + ttl, updated_at=func.now())
    )


async def release_reservations(
    db: AsyncSession, session_id: uuid.UUID, reason: str
) -> None:
    """Give back everything a session is holding."""
    await _release(db, session_id, None, reason)


async def release_expired(db: AsyncSession) -> int:
    """Mark expired holds as released. Returns how many.

    Housekeeping only. Expired holds already count for nothing, so stock is
    never wrong because this has not run.
    """
    result = await db.execute(
        update(Reservation)
        .where(
            Reservation.status == ReservationStatus.ACTIVE,
            Reservation.expires_at <= func.now(),
        )
        .values(
            status=ReservationStatus.RELEASED,
            released_reason=EXPIRED,
            updated_at=func.now(),
        )
        .returning(Reservation.id)
    )
    return len(result.all())


# --- Taking stock when an order is placed -----------------------------------


async def commit_reservations(
    db: AsyncSession, session_id: uuid.UUID, needs: Collection[StockNeed]
) -> None:
    """Take the stock for a submitted order.

    Availability is checked again here, under the lock, for every item. A
    session whose hold expired while it was quiet may find the stock gone;
    then InsufficientStock is raised and nothing is taken.
    """
    inventories = await _lock_inventory(db, [need.item_id for need in needs])
    tracked = [need for need in needs if need.item_id in inventories]

    # Check everything before changing anything.
    for need in tracked:
        on_hand = inventories[need.item_id].on_hand
        available = on_hand - await _held_by_others(db, need.item_id, session_id)
        if need.quantity > available:
            raise InsufficientStock(need.item_code, need.quantity, max(0, available))

    for need in tracked:
        inventories[need.item_id].on_hand -= need.quantity
    await db.execute(
        update(Reservation)
        .where(
            Reservation.session_id == session_id,
            Reservation.status == ReservationStatus.ACTIVE,
        )
        .values(status=ReservationStatus.COMMITTED, updated_at=func.now())
    )
    await db.flush()


# --- Reading and setting stock ----------------------------------------------


async def stock_levels(db: AsyncSession) -> list[StockLevel]:
    """Every stock-tracked item with what is there and what is held."""
    reserved = (
        select(Reservation.item_id, func.sum(Reservation.quantity).label("quantity"))
        .where(*_LIVE)
        .group_by(Reservation.item_id)
        .subquery()
    )
    statement = (
        select(MenuItem.code, Inventory.on_hand, func.coalesce(reserved.c.quantity, 0))
        .join(MenuItem, MenuItem.id == Inventory.item_id)
        .outerjoin(reserved, reserved.c.item_id == Inventory.item_id)
        .order_by(MenuItem.code)
    )
    return [
        StockLevel(item_code=code, on_hand=on_hand, reserved=int(held))
        for code, on_hand, held in (await db.execute(statement)).all()
    ]


async def sold_out_item_ids(db: AsyncSession) -> set[uuid.UUID]:
    """Stock-tracked items with nothing left that is not already held."""
    reserved = (
        select(Reservation.item_id, func.sum(Reservation.quantity).label("quantity"))
        .where(*_LIVE)
        .group_by(Reservation.item_id)
        .subquery()
    )
    statement = (
        select(Inventory.item_id)
        .outerjoin(reserved, reserved.c.item_id == Inventory.item_id)
        .where(Inventory.on_hand - func.coalesce(reserved.c.quantity, 0) <= 0)
    )
    return set((await db.execute(statement)).scalars())


async def set_on_hand(db: AsyncSession, item_code: str, on_hand: int) -> StockLevel:
    """Record how many of an item are there, starting to track it if needed."""
    item_id = (
        await db.execute(select(MenuItem.id).where(MenuItem.code == item_code))
    ).scalar_one_or_none()
    if item_id is None:
        raise UnknownItem(item_code)
    await db.execute(
        insert(Inventory)
        .values(item_id=item_id, on_hand=on_hand)
        .on_conflict_do_update(
            index_elements=["item_id"],
            set_={"on_hand": on_hand, "updated_at": func.now()},
        )
    )
    return next(
        level for level in await stock_levels(db) if level.item_code == item_code
    )


# --- Helpers ----------------------------------------------------------------


async def _lock_inventory(
    db: AsyncSession, item_ids: Collection[uuid.UUID]
) -> Mapping[uuid.UUID, Inventory]:
    """Lock the inventory rows for these items until the transaction ends.

    Rows are always locked in the same order, so two transactions that each
    need several items cannot end up waiting on each other.
    """
    if not item_ids:
        return {}
    statement = (
        select(Inventory)
        .where(Inventory.item_id.in_(item_ids))
        .order_by(Inventory.item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {row.item_id: row for row in (await db.execute(statement)).scalars()}


async def _held_by_others(
    db: AsyncSession, item_id: uuid.UUID, session_id: uuid.UUID
) -> int:
    statement = select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
        Reservation.item_id == item_id,
        Reservation.session_id != session_id,
        *_LIVE,
    )
    return int((await db.execute(statement)).scalar_one())


async def _held_by_session(
    db: AsyncSession, item_id: uuid.UUID, session_id: uuid.UUID
) -> int:
    statement = select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
        Reservation.item_id == item_id,
        Reservation.session_id == session_id,
        *_LIVE,
    )
    return int((await db.execute(statement)).scalar_one())


async def _release(
    db: AsyncSession,
    session_id: uuid.UUID,
    item_ids: Collection[uuid.UUID] | None,
    reason: str,
) -> None:
    statement = (
        update(Reservation)
        .where(
            Reservation.session_id == session_id,
            Reservation.status == ReservationStatus.ACTIVE,
        )
        .values(
            status=ReservationStatus.RELEASED,
            released_reason=reason,
            updated_at=func.now(),
        )
    )
    if item_ids is not None:
        statement = statement.where(Reservation.item_id.in_(item_ids))
    await db.execute(statement)
