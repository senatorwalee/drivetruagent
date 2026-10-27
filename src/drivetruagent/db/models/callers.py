"""Phone numbers that have called, so a caller's recent orders can be found.

Only what remembering needs is kept: the number and when it called. No name,
address or payment details. A caller's orders are found through the sessions
that link to it; nothing about an order is copied here.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.enums import CallerStatus


class Caller(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "callers"
    __table_args__ = (
        # E.164: a plus and 8 to 15 digits, such as +14165550123.
        CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_is_e164"),
        CheckConstraint("call_count >= 1", name="call_count_positive"),
        enum_check("status", CallerStatus),
        # Callers not heard from in a long time are deleted. See retention.
        Index("ix_callers_last_call_at", "last_call_at"),
    )

    phone_e164: Mapped[str] = mapped_column(unique=True)
    first_call_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_call_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    call_count: Mapped[int] = mapped_column(server_default=text("1"))
    # The caller said "don't save my number": their orders are never read back
    # to them. The number is still kept, as the greeting says, for pickups.
    history_opted_out: Mapped[bool] = mapped_column(server_default=text("false"))
    # What staff decided. Having no-shows flags a number without this being
    # changed: that is worked out from the orders whenever it is needed (see
    # policy/caller_rules.py), so it clears itself as old no-shows expire.
    status: Mapped[CallerStatus] = mapped_column(
        text_enum(CallerStatus), server_default=text("'normal'")
    )


class CallerStatusChange(UuidPrimaryKey, Base):
    """Who changed a number's status, when, and why. Never changed afterwards."""

    __tablename__ = "caller_status_changes"
    __table_args__ = (
        enum_check("from_status", CallerStatus),
        enum_check("to_status", CallerStatus),
        Index(
            "ix_caller_status_changes_caller_id_changed_at", "caller_id", "changed_at"
        ),
    )

    # Deleted with the caller: once a number is forgotten, so is its history.
    caller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("callers.id", ondelete="CASCADE")
    )
    from_status: Mapped[CallerStatus] = mapped_column(text_enum(CallerStatus))
    to_status: Mapped[CallerStatus] = mapped_column(text_enum(CallerStatus))
    reason: Mapped[str]
    changed_by: Mapped[str]
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
