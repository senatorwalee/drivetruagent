"""Sessions handed to a person, and what became of them."""

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
from drivetruagent.enums import HandoffOutcome, HandoffStatus


class Handoff(UuidPrimaryKey, Timestamps, Base):
    """One session a person has been asked to take over.

    Only why it was handed off and what staff then did are stored. What the
    person needs to see (the cart, the conversation, the stock held) is read
    from the session's own rows when asked for: a handed-off session never
    changes again, so there is nothing here to keep in step.
    """

    __tablename__ = "handoffs"
    __table_args__ = (
        enum_check("status", HandoffStatus),
        enum_check("outcome", HandoffOutcome),
        # resolved_at and outcome are set exactly when it has been resolved.
        CheckConstraint(
            "(resolved_at IS NULL) = (status <> 'resolved')",
            name="resolved_at_matches_status",
        ),
        CheckConstraint(
            "(outcome IS NULL) = (status <> 'resolved')",
            name="outcome_matches_status",
        ),
        # The queue: what is waiting, longest first.
        Index("ix_handoffs_status_created_at", "status", "created_at"),
    )

    # Unique: a session is handed off once.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    # The code of the rule that decided: extreme_quantity, time_limit, ...
    reason: Mapped[str]
    # What that rule knew: quantities, or the words given as the reason.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    # The proposed action that triggered it, if there was one.
    action: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[HandoffStatus] = mapped_column(
        text_enum(HandoffStatus), server_default=text("'open'")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None]
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[HandoffOutcome | None] = mapped_column(text_enum(HandoffOutcome))
    note: Mapped[str | None]
