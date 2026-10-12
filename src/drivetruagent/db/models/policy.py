"""Actions held back until the customer confirms them."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.enums import PendingActionStatus


class PendingAction(UuidPrimaryKey, Timestamps, Base):
    """An action the policy engine wants the customer to confirm first.

    The action itself is stored. When the customer says yes, this stored copy
    is what runs, so it cannot be altered between asking and confirming.
    """

    __tablename__ = "pending_actions"
    __table_args__ = (
        enum_check("status", PendingActionStatus),
        CheckConstraint("cart_version >= 0", name="cart_version_non_negative"),
        # resolved_at is set exactly when the action is no longer pending.
        CheckConstraint(
            "(resolved_at IS NULL) = (status = 'pending')",
            name="resolved_at_matches_status",
        ),
        # One open question per session: "yes" must never be ambiguous.
        Index(
            "uq_pending_actions_one_pending_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    # The proposed action as JSON, with its `type` tag.
    action: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Which policy rule asked for confirmation, and what the agent was told.
    rule: Mapped[str]
    message: Mapped[str]
    status: Mapped[PendingActionStatus] = mapped_column(
        text_enum(PendingActionStatus), server_default=text("'pending'")
    )
    # The cart version when this was created. If the cart has moved on by the
    # time the customer answers, the answer no longer applies.
    cart_version: Mapped[int]
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
