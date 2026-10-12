"""create catalog tables

Revision ID: 63f7e08598a6
Revises:
Create Date: 2026-10-12 17:09:13.946005

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "63f7e08598a6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_categories",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_menu_categories_name_i18n_has_en"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_menu_categories")),
        sa.UniqueConstraint("code", name=op.f("uq_menu_categories_code")),
    )
    op.create_table(
        "menu_items",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("name_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "description_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("base_price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "description_i18n IS NULL "
            "OR jsonb_typeof(description_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_menu_items_description_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_menu_items_name_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "base_price_cents >= 0",
            name=op.f("ck_menu_items_base_price_cents_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["menu_categories.id"],
            name=op.f("fk_menu_items_category_id_menu_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_menu_items")),
        sa.UniqueConstraint("code", name=op.f("uq_menu_items_code")),
    )
    op.create_index(
        op.f("ix_menu_items_category_id"), "menu_items", ["category_id"], unique=False
    )
    op.create_table(
        "menu_item_aliases",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "language",
            sa.Enum("en", "fr", name="language", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "alias <> ''", name=op.f("ck_menu_item_aliases_alias_not_empty")
        ),
        sa.CheckConstraint(
            "language IN ('en', 'fr')", name=op.f("ck_menu_item_aliases_language_valid")
        ),
        sa.CheckConstraint(
            "alias = lower(btrim(alias))",
            name=op.f("ck_menu_item_aliases_alias_normalised"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_menu_item_aliases_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_menu_item_aliases")),
        sa.UniqueConstraint(
            "item_id",
            "language",
            "alias",
            name=op.f("uq_menu_item_aliases_item_id_language_alias"),
        ),
    )
    op.create_index(
        "ix_menu_item_aliases_language_alias",
        "menu_item_aliases",
        ["language", "alias"],
        unique=False,
    )
    op.create_table(
        "option_groups",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("min_select", sa.Integer(), nullable=False),
        sa.Column("max_select", sa.Integer(), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_option_groups_name_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "max_select >= 1", name=op.f("ck_option_groups_max_select_positive")
        ),
        sa.CheckConstraint(
            "max_select >= min_select",
            name=op.f("ck_option_groups_max_select_covers_min"),
        ),
        sa.CheckConstraint(
            "min_select >= 0", name=op.f("ck_option_groups_min_select_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_option_groups_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_option_groups")),
        sa.UniqueConstraint(
            "item_id", "code", name=op.f("uq_option_groups_item_id_code")
        ),
    )
    op.create_table(
        "options",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("option_group_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "price_delta_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_options_name_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "price_delta_cents >= 0",
            name=op.f("ck_options_price_delta_cents_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["option_group_id"],
            ["option_groups.id"],
            name=op.f("fk_options_option_group_id_option_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_options")),
        sa.UniqueConstraint(
            "option_group_id", "code", name=op.f("uq_options_option_group_id_code")
        ),
    )
    op.create_index(
        "uq_options_one_default_per_group",
        "options",
        ["option_group_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_options_one_default_per_group",
        table_name="options",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_table("options")
    op.drop_table("option_groups")
    op.drop_index("ix_menu_item_aliases_language_alias", table_name="menu_item_aliases")
    op.drop_table("menu_item_aliases")
    op.drop_index(op.f("ix_menu_items_category_id"), table_name="menu_items")
    op.drop_table("menu_items")
    op.drop_table("menu_categories")
