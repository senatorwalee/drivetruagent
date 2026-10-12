"""The menu: categories, items, the choices each item offers, and aliases.

The menu is data, not prompt text. The agent learns what exists and which
choices an item needs by reading these tables through tools.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from drivetruagent.db.base import (
    Base,
    Timestamps,
    UuidPrimaryKey,
    enum_check,
    has_english,
    text_enum,
)
from drivetruagent.enums import Language

# Translated text, one entry per language: {"en": "Fries", "fr": "Frites"}.
type LocalizedText = dict[str, str]

# none_as_null: store Python None as SQL NULL. The default would store the JSON
# value `null`, which is not NULL to the database and fails the English check.
LOCALIZED_TEXT = JSONB(none_as_null=True)


class MenuCategory(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "menu_categories"
    __table_args__ = (has_english("name_i18n"),)

    code: Mapped[str] = mapped_column(unique=True)
    name_i18n: Mapped[LocalizedText] = mapped_column(LOCALIZED_TEXT)
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    items: Mapped[list["MenuItem"]] = relationship(
        back_populates="category", lazy="raise", order_by="MenuItem.sort_order"
    )


class MenuItem(UuidPrimaryKey, Timestamps, Base):
    __tablename__ = "menu_items"
    __table_args__ = (
        has_english("name_i18n"),
        has_english("description_i18n"),
        CheckConstraint("base_price_cents >= 0", name="base_price_cents_non_negative"),
    )

    code: Mapped[str] = mapped_column(unique=True)
    # RESTRICT: a category cannot be deleted while items still belong to it.
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_categories.id", ondelete="RESTRICT"), index=True
    )
    name_i18n: Mapped[LocalizedText] = mapped_column(LOCALIZED_TEXT)
    description_i18n: Mapped[LocalizedText | None] = mapped_column(LOCALIZED_TEXT)
    # The price with the cheapest choices; options add to it.
    base_price_cents: Mapped[int]
    # Items are never deleted, because orders point at them. Deactivate instead.
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    category: Mapped[MenuCategory] = relationship(back_populates="items", lazy="raise")
    option_groups: Mapped[list["OptionGroup"]] = relationship(
        back_populates="item",
        lazy="raise",
        order_by="OptionGroup.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases: Mapped[list["MenuItemAlias"]] = relationship(
        back_populates="item",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OptionGroup(UuidPrimaryKey, Timestamps, Base):
    """One choice a customer makes about an item: size, style, flavour.

    min_select and max_select say how many options may be picked. A group with
    min_select >= 1 and no default option is one the agent has to ask about.
    """

    __tablename__ = "option_groups"
    __table_args__ = (
        # Also serves lookups by item_id, so no separate index is needed.
        UniqueConstraint("item_id", "code"),
        has_english("name_i18n"),
        CheckConstraint("min_select >= 0", name="min_select_non_negative"),
        CheckConstraint("max_select >= 1", name="max_select_positive"),
        CheckConstraint("max_select >= min_select", name="max_select_covers_min"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE")
    )
    code: Mapped[str]
    name_i18n: Mapped[LocalizedText] = mapped_column(LOCALIZED_TEXT)
    min_select: Mapped[int]
    max_select: Mapped[int]
    # The order the agent asks about groups in.
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    item: Mapped[MenuItem] = relationship(back_populates="option_groups", lazy="raise")
    options: Mapped[list["Option"]] = relationship(
        back_populates="group",
        lazy="raise",
        order_by="Option.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Option(UuidPrimaryKey, Timestamps, Base):
    """One allowed value inside an option group: small, curly, vanilla."""

    __tablename__ = "options"
    __table_args__ = (
        UniqueConstraint("option_group_id", "code"),
        has_english("name_i18n"),
        CheckConstraint(
            "price_delta_cents >= 0", name="price_delta_cents_non_negative"
        ),
        # At most one default per group: uniqueness applies only to default rows.
        Index(
            "uq_options_one_default_per_group",
            "option_group_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    option_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("option_groups.id", ondelete="CASCADE")
    )
    code: Mapped[str]
    name_i18n: Mapped[LocalizedText] = mapped_column(LOCALIZED_TEXT)
    price_delta_cents: Mapped[int] = mapped_column(server_default=text("0"))
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(server_default=text("0"))

    group: Mapped[OptionGroup] = relationship(back_populates="options", lazy="raise")


class MenuItemAlias(UuidPrimaryKey, Timestamps, Base):
    """Something else customers call an item, per language: "coke", "frites".

    The same alias may point at several items ("burger"); the search tool then
    returns every candidate and the agent asks which one was meant.
    """

    __tablename__ = "menu_item_aliases"
    __table_args__ = (
        UniqueConstraint("item_id", "language", "alias"),
        enum_check("language", Language),
        CheckConstraint("alias = lower(btrim(alias))", name="alias_normalised"),
        CheckConstraint("alias <> ''", name="alias_not_empty"),
        Index("ix_menu_item_aliases_language_alias", "language", "alias"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE")
    )
    language: Mapped[Language] = mapped_column(text_enum(Language))
    alias: Mapped[str]

    item: Mapped[MenuItem] = relationship(back_populates="aliases", lazy="raise")
