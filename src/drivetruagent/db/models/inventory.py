"""Stock, the holds sessions place on it, and the record of repeated requests."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.enums import ReservationStatus


class Inventory(Timestamps, Base):
    """How many of an item are physically there.

    One row per stock-tracked item. An item with no row is not tracked and is
    treated as unlimited.
    """

    __tablename__ = "inventory"
    __table_args__ = (
        # The last line of defence: whatever the code does, the database will
        # not record stock below zero.
        CheckConstraint("on_hand >= 0", name="on_hand_non_negative"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    on_hand: Mapped[int]


class Reservation(UuidPrimaryKey, Timestamps, Base):
    """Units of an item a session is holding while it orders.

    One row per session and item, holding the total across that session's
    cart lines. It counts against what other sessions can have only while it
    is active and has not expired.
    """

    __tablename__ = "reservations"
    __table_args__ = (
        enum_check("status", ReservationStatus),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        Index(
            "uq_reservations_one_active_per_session_item",
            "session_id",
            "item_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # "What do other sessions hold of this item?" is asked on every change.
        Index(
            "ix_reservations_item_id_active",
            "item_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT")
    )
    quantity: Mapped[int]
    status: Mapped[ReservationStatus] = mapped_column(
        text_enum(ReservationStatus), server_default=text("'active'")
    )
    # After this moment the hold no longer counts, whether or not anything
    # has come along to mark it released.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_reason: Mapped[str | None]


class IdempotencyRecord(UuidPrimaryKey, Timestamps, Base):
    """The stored answer to a request, so a repeat gets the same answer.

    Written in the same transaction as the work it describes: either both
    exist or neither does.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("session_id", "key"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    # Chosen by the caller. It names one request.
    key: Mapped[str]
    operation: Mapped[str]
    # A hash of the request, to notice a key being reused for something else.
    request_fingerprint: Mapped[str]
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
