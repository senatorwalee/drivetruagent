"""add the allergen matrix

Revision ID: 1b99e6435dcc
Revises: df3652adf06d
Create Date: 2026-10-14 11:49:09.248004

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1b99e6435dcc"
down_revision: str | Sequence[str] | None = "df3652adf06d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "allergen_declarations",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("option_id", sa.Uuid(), nullable=True),
        sa.Column(
            "allergen",
            sa.Enum(
                "peanut",
                "tree_nut",
                "sesame",
                "milk",
                "egg",
                "fish",
                "shellfish",
                "soy",
                "wheat",
                "gluten",
                "mustard",
                "sulphite",
                name="allergen",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "level",
            sa.Enum(
                "contains",
                "may_contain",
                name="allergenlevel",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "note_i18n",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
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
            "allergen IN ('peanut', 'tree_nut', 'sesame', 'milk', 'egg', 'fish', "
            "'shellfish', 'soy', 'wheat', 'gluten', 'mustard', 'sulphite')",
            name=op.f("ck_allergen_declarations_allergen_valid"),
        ),
        sa.CheckConstraint(
            "level IN ('contains', 'may_contain')",
            name=op.f("ck_allergen_declarations_level_valid"),
        ),
        sa.CheckConstraint(
            "note_i18n IS NULL OR "
            "jsonb_typeof(note_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_allergen_declarations_note_i18n_has_en"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_allergen_declarations_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["options.id"],
            name=op.f("fk_allergen_declarations_option_id_options"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_allergen_declarations")),
        sa.UniqueConstraint(
            "item_id",
            "option_id",
            "allergen",
            name=op.f("uq_allergen_declarations_item_id_option_id_allergen"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_allergen_declarations_item_id"),
        "allergen_declarations",
        ["item_id"],
        unique=False,
    )
    op.add_column(
        "menu_items", sa.Column("allergens_reviewed_on", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("menu_items", "allergens_reviewed_on")
    op.drop_index(
        op.f("ix_allergen_declarations_item_id"), table_name="allergen_declarations"
    )
    op.drop_table("allergen_declarations")
