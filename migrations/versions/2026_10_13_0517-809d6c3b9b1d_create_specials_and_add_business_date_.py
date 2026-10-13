"""create specials and add business date to sessions

Revision ID: 809d6c3b9b1d
Revises: bf474abf4b65
Create Date: 2026-10-13 05:17:39.464482

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "809d6c3b9b1d"
down_revision: str | Sequence[str] | None = "bf474abf4b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "specials",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "name_i18n",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "description_i18n",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "weekdays",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
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
            "description_i18n IS NULL "
            "OR jsonb_typeof(description_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_specials_description_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_specials_name_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "discount_cents > 0", name=op.f("ck_specials_discount_cents_positive")
        ),
        sa.CheckConstraint(
            "weekdays <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]",
            name=op.f("ck_specials_weekdays_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_specials_item_id_menu_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_specials")),
        sa.UniqueConstraint("code", name=op.f("uq_specials_code")),
    )
    op.create_index(op.f("ix_specials_item_id"), "specials", ["item_id"], unique=False)
    # Added in three steps so the migration works on a table that has rows:
    # allow NULL, fill in existing sessions, then require a value. Existing
    # sessions get the date they started on in the default restaurant
    # timezone. New sessions are given their date by the application.
    op.add_column("sessions", sa.Column("business_date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE sessions "
        "SET business_date = (started_at AT TIME ZONE 'America/Toronto')::date"
    )
    op.alter_column("sessions", "business_date", nullable=False)


def downgrade() -> None:
    op.drop_column("sessions", "business_date")
    op.drop_index(op.f("ix_specials_item_id"), table_name="specials")
    op.drop_table("specials")
