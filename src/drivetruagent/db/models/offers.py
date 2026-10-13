"""Combos, and the record of what has been suggested to a session.

A combo is a rule, not a product: "one burger, one side and one drink bought
together cost less". Nothing about it is stored on a cart. Whether a cart
earns a combo is worked out each time the cart is priced.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    has_english,
    text_enum,
)
from drivetruagent.db.models.catalog import LOCALIZED_TEXT, LocalizedText, MenuCategory
from drivetruagent.enums import DiscountKind, SuggestionStatus


class ComboRule(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "combo_rules"
    __table_args__ = (
        has_english("name_i18n"),
        CheckConstraint("discount_cents > 0", name="discount_cents_positive"),
    )

    code: Mapped[str] = mapped_column(unique=True)
    name_i18n: Mapped[LocalizedText] = mapped_column(LOCALIZED_TEXT)
    # Taken off each time the combo is made: two complete sets, twice.
    discount_cents: Mapped[int]
    # Rules are never deleted, so a retired one can be brought back.
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    # Priority. When units could count toward two rules, the lower goes first.
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    slots: Mapped[list["ComboRuleSlot"]] = relationship(
        back_populates="rule",
        lazy="raise",
        order_by="ComboRuleSlot.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ComboRuleSlot(UuidPrimaryKey, Timestamps, Base):
    """One unit a combo needs: any single item from a category.

    The slot at position 0 is the main item, the one the combo is built
    around. A combo is only ever suggested to a cart that already has it.
    """

    __tablename__ = "combo_rule_slots"
    __table_args__ = (
        # Also serves lookups by combo_rule_id.
        UniqueConstraint("combo_rule_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    combo_rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("combo_rules.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    # RESTRICT: a category cannot be deleted while a combo is built on it.
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_categories.id", ondelete="RESTRICT"), index=True
    )

    rule: Mapped[ComboRule] = relationship(back_populates="slots", lazy="raise")
    category: Mapped[MenuCategory] = relationship(lazy="raise")


class SessionSuggestion(UuidPrimaryKey, Timestamps, Base):
    """Something the agent was told it may offer a customer, once.

    One row per session and offer. It is what stops the same combo being
    pushed at a customer who has already heard about it.
    """

    __tablename__ = "session_suggestions"
    __table_args__ = (
        # Also serves lookups by session_id.
        UniqueConstraint("session_id", "kind", "code"),
        enum_check("kind", DiscountKind),
        enum_check("status", SuggestionStatus),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    kind: Mapped[DiscountKind] = mapped_column(text_enum(DiscountKind))
    code: Mapped[str]
    status: Mapped[SuggestionStatus] = mapped_column(
        text_enum(SuggestionStatus), server_default=text("'open'")
    )
