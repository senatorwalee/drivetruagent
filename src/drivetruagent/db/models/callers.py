"""Phone numbers that have called, so a caller's recent orders can be found.

Only what remembering needs is kept: the number and when it called. No name,
address or payment details. A caller's orders are found through the sessions
that link to it; nothing about an order is copied here.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column

from drivetruagent.db.base import Base, Timestamps, UuidPrimaryKey


class Caller(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "callers"
    __table_args__ = (
        # E.164: a plus and 8 to 15 digits, such as +14165550123.
        CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'", name="phone_is_e164"),
        CheckConstraint("call_count >= 1", name="call_count_positive"),
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
