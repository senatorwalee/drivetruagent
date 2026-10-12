"""add group name and position to frozen options

Revision ID: 49c8dd6c5805
Revises: 41f5bd144077
Create Date: 2026-10-12 18:43:32.400238

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49c8dd6c5805"
down_revision: str | Sequence[str] | None = "41f5bd144077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Each column is added with a temporary default, so the migration also
    # works on a table that already holds rows. The default is then removed:
    # new rows must supply real values.
    op.add_column(
        "order_line_options",
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "order_line_options",
        sa.Column("group_name", sa.String(), server_default="", nullable=False),
    )
    op.alter_column("order_line_options", "position", server_default=None)
    op.alter_column("order_line_options", "group_name", server_default=None)
    # Autogenerate does not detect CHECK constraints; this one is added by hand.
    op.create_check_constraint(
        op.f("ck_order_line_options_position_non_negative"),
        "order_line_options",
        "position >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_order_line_options_position_non_negative"),
        "order_line_options",
        type_="check",
    )
    op.drop_column("order_line_options", "group_name")
    op.drop_column("order_line_options", "position")
