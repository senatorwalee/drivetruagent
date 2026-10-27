"""add diet suitability

Revision ID: 979a8c4b6785
Revises: 5c2f9a7e1d43
Create Date: 2026-10-24 17:21:11.610447

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "979a8c4b6785"
down_revision: str | Sequence[str] | None = "5c2f9a7e1d43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diet_suitability",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "diet",
            sa.Enum("halal", "kosher", name="diet", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "certified", "suitable", name="dietstatus", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "note_i18n",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
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
            "diet IN ('halal', 'kosher')", name=op.f("ck_diet_suitability_diet_valid")
        ),
        sa.CheckConstraint(
            "note_i18n IS NULL OR "
            "jsonb_typeof(note_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_diet_suitability_note_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "status IN ('certified', 'suitable')",
            name=op.f("ck_diet_suitability_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_diet_suitability_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diet_suitability")),
        sa.UniqueConstraint(
            "item_id", "diet", name=op.f("uq_diet_suitability_item_id_diet")
        ),
    )
    op.create_index(
        op.f("ix_diet_suitability_item_id"),
        "diet_suitability",
        ["item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_diet_suitability_item_id"), table_name="diet_suitability")
    op.drop_table("diet_suitability")
