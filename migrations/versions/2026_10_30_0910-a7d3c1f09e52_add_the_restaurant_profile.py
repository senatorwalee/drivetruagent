"""add the restaurant profile

Revision ID: a7d3c1f09e52
Revises: 03620e2eae11
Create Date: 2026-10-30 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d3c1f09e52"
down_revision: str | Sequence[str] | None = "03620e2eae11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restaurant_profile",
        sa.Column(
            "id",
            sa.Integer(),
            server_default=sa.text("1"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("pickup_minutes", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_restaurant_profile_one_row")),
        sa.CheckConstraint(
            "pickup_minutes > 0",
            name=op.f("ck_restaurant_profile_pickup_minutes_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_profile")),
    )


def downgrade() -> None:
    op.drop_table("restaurant_profile")
