"""A customer's visit and the cart they are building.

The cart stores choices, not prices: which item, how many, which options.
Prices are worked out from the menu each time, so a cart can never hold a
stale total. Prices are frozen only when the cart becomes an order.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    text_enum,
)
from drivetruagent.db.models.catalog import MenuItem, Option
from drivetruagent.enums import ACTIVE_SESSION_STATES, Language, SessionState

# SQL list of the in-progress states, e.g. 'confirming', 'greeting', 'ordering'.
_ACTIVE_STATES_SQL = ", ".join(
    f"'{state.value}'" for state in sorted(ACTIVE_SESSION_STATES)
)


class CustomerSession(UuidPrimaryKey, Timestamps, Base):
    """One car at the speaker, from greeting to a final outcome."""

    __tablename__ = "sessions"
    __table_args__ = (
        enum_check("state", SessionState),
        enum_check("language", Language),
        # ended_at is set exactly when the session has reached a final state.
        CheckConstraint(
            f"(ended_at IS NULL) = (state IN ({_ACTIVE_STATES_SQL}))",
            name="ended_at_matches_state",
        ),
        CheckConstraint("paused_seconds >= 0", name="paused_seconds_non_negative"),
        # One car per lane at a time: uniqueness applies only to active sessions.
        Index(
            "uq_sessions_one_active_per_lane",
            "lane",
            unique=True,
            postgresql_where=text(f"state IN ({_ACTIVE_STATES_SQL})"),
        ),
    )

    lane: Mapped[str] = mapped_column(server_default=text("'lane-1'"))
    state: Mapped[SessionState] = mapped_column(
        text_enum(SessionState), server_default=text("'greeting'")
    )
    # Null until the customer's language has been detected.
    language: Mapped[Language | None] = mapped_column(text_enum(Language))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None]
    # The restaurant's calendar date when the car arrived. It decides which
    # specials the session gets, and does not change if midnight passes.
    business_date: Mapped[date]

    # The session's clocks. What they mean is in ordering/timers.py.
    # When either side last said something. Silence is measured from here.
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When "are you still there?" was said for the current silence.
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set while the agent is working on a reply. The clocks do not run then.
    turn_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set while a presenter has paused the session.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Total time spent paused. It does not count toward the length limit.
    paused_seconds: Mapped[int] = mapped_column(server_default=text("0"))

    cart: Mapped["Cart | None"] = relationship(
        back_populates="session",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Cart(UuidPrimaryKey, Timestamps, Base):
    """The order being built. Exactly one per session."""

    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint("version >= 0", name="version_non_negative"),
        CheckConstraint("next_line_no >= 1", name="next_line_no_positive"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    # Goes up by one on every change. Lets a screen ignore a snapshot that
    # arrives out of order, and lets code notice the cart changed under it.
    version: Mapped[int] = mapped_column(server_default=text("0"))
    # The number the next new line will get. Kept here, not derived from the
    # lines, so a removed line's number is never handed out again.
    next_line_no: Mapped[int] = mapped_column(server_default=text("1"))

    session: Mapped[CustomerSession] = relationship(back_populates="cart", lazy="raise")
    lines: Mapped[list["CartLine"]] = relationship(
        back_populates="cart",
        lazy="raise",
        order_by="CartLine.line_no",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CartLine(UuidPrimaryKey, Timestamps, Base):
    """One item in the cart, with a quantity and the options chosen for it."""

    __tablename__ = "cart_lines"
    __table_args__ = (
        # Also serves lookups by cart_id, so no separate index is needed.
        UniqueConstraint("cart_id", "line_no"),
        CheckConstraint("line_no >= 1", name="line_no_positive"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
    )

    cart_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE")
    )
    # How the agent and the customer refer to a line: L1, L2, "the second one".
    line_no: Mapped[int]
    # RESTRICT: a menu item in someone's cart cannot be deleted from the menu.
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int]

    cart: Mapped[Cart] = relationship(back_populates="lines", lazy="raise")
    item: Mapped[MenuItem] = relationship(lazy="raise")
    selected_options: Mapped[list["CartLineOption"]] = relationship(
        back_populates="line",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CartLineOption(Base):
    """An option chosen for a cart line.

    The database cannot cheaply check that the option belongs to the line's
    item, or that the number chosen per group is allowed. The cart logic
    validates both before writing.
    """

    __tablename__ = "cart_line_options"

    # The pair is the primary key, so the same option cannot be chosen twice.
    cart_line_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cart_lines.id", ondelete="CASCADE"), primary_key=True
    )
    option_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("options.id", ondelete="RESTRICT"), primary_key=True, index=True
    )

    line: Mapped[CartLine] = relationship(
        back_populates="selected_options", lazy="raise"
    )
    option: Mapped[Option] = relationship(lazy="raise")
