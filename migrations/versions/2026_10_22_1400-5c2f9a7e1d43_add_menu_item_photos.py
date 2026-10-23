"""add menu item photos

Revision ID: 5c2f9a7e1d43
Revises: 9319f25b4758
Create Date: 2026-10-22 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c2f9a7e1d43"
down_revision: str | Sequence[str] | None = "9319f25b4758"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_item_photos",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("credit", sa.String(), nullable=True),
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
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name=op.f("ck_menu_item_photos_content_type_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(data) > 0", name=op.f("ck_menu_item_photos_data_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_menu_item_photos_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_menu_item_photos")),
    )


def downgrade() -> None:
    op.drop_table("menu_item_photos")
