"""create handoffs

Revision ID: 4275d30f1796
Revises: e1c6c8be2ce5
Create Date: 2026-10-13 09:30:44.324682

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4275d30f1796"
down_revision: str | Sequence[str] | None = "e1c6c8be2ce5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "action",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "acknowledged",
                "resolved",
                name="handoffstatus",
                native_enum=False,
                length=32,
            ),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "order_placed",
                "cancelled",
                name="handoffoutcome",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.String(), nullable=True),
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
            "(outcome IS NULL) = (status <> 'resolved')",
            name=op.f("ck_handoffs_outcome_matches_status"),
        ),
        sa.CheckConstraint(
            "(resolved_at IS NULL) = (status <> 'resolved')",
            name=op.f("ck_handoffs_resolved_at_matches_status"),
        ),
        sa.CheckConstraint(
            "outcome IN ('order_placed', 'cancelled')",
            name=op.f("ck_handoffs_outcome_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')",
            name=op.f("ck_handoffs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_handoffs_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoffs")),
        sa.UniqueConstraint("session_id", name=op.f("uq_handoffs_session_id")),
    )
    op.create_index(
        "ix_handoffs_status_created_at",
        "handoffs",
        ["status", "created_at"],
        unique=False,
    )
    # Sessions handed off before this table existed get a handoff too, so
    # "every handed-off session has one" is true from the start. They are
    # recorded as closed: nobody is waiting on them now.
    op.execute(
        """
        INSERT INTO handoffs
            (session_id, reason, status, resolved_at, outcome, note, created_at)
        SELECT id,
               split_part(coalesce(end_reason, 'unknown'), ':', 1),
               'resolved',
               ended_at,
               'cancelled',
               'Handed off before handoffs were recorded.',
               ended_at
        FROM sessions
        WHERE state = 'handed_off'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_handoffs_status_created_at", table_name="handoffs")
    op.drop_table("handoffs")
