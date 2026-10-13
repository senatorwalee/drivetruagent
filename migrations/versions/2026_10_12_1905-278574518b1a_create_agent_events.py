"""create agent events

Revision ID: 278574518b1a
Revises: 8536b71abbc3
Create Date: 2026-10-12 19:05:41.036455

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "278574518b1a"
down_revision: str | Sequence[str] | None = "8536b71abbc3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_events",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "user_message",
                "model_call",
                "tool_call",
                "tool_result",
                "assistant_message",
                name="agenteventkind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            "kind IN ('user_message', 'model_call', 'tool_call', "
            "'tool_result', 'assistant_message')",
            name=op.f("ck_agent_events_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_agent_events_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_agent_events_seq")),
    )
    op.create_index(
        "ix_agent_events_session_id_seq",
        "agent_events",
        ["session_id", "seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_session_id_seq", table_name="agent_events")
    op.drop_table("agent_events")
