"""create pending actions

Revision ID: 8536b71abbc3
Revises: 49c8dd6c5805
Create Date: 2026-10-12 18:52:28.647631

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8536b71abbc3"
down_revision: str | Sequence[str] | None = "49c8dd6c5805"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                "cancelled",
                "superseded",
                "escalated",
                name="pendingactionstatus",
                native_enum=False,
                length=32,
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("cart_version", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "(resolved_at IS NULL) = (status = 'pending')",
            name=op.f("ck_pending_actions_resolved_at_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled', "
            "'superseded', 'escalated')",
            name=op.f("ck_pending_actions_status_valid"),
        ),
        sa.CheckConstraint(
            "cart_version >= 0",
            name=op.f("ck_pending_actions_cart_version_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_pending_actions_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_actions")),
    )
    op.create_index(
        op.f("ix_pending_actions_session_id"),
        "pending_actions",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "uq_pending_actions_one_pending_per_session",
        "pending_actions",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pending_actions_one_pending_per_session",
        table_name="pending_actions",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_pending_actions_session_id"), table_name="pending_actions")
    op.drop_table("pending_actions")
