"""add session clocks and the timer event kind

Revision ID: e1c6c8be2ce5
Revises: 809d6c3b9b1d
Create Date: 2026-10-13 07:47:21.591096

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c6c8be2ce5"
down_revision: str | Sequence[str] | None = "809d6c3b9b1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "sessions", sa.Column("nudged_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sessions",
        sa.Column("turn_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sessions",
        sa.Column(
            "paused_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    # Sessions that already exist were last active when they ended, or, if
    # they never did, when they started. Not at the moment of this migration.
    op.execute("UPDATE sessions SET last_activity_at = COALESCE(ended_at, started_at)")
    # Autogenerate does not detect CHECK constraints; these are written by hand.
    op.create_check_constraint(
        op.f("ck_sessions_paused_seconds_non_negative"),
        "sessions",
        "paused_seconds >= 0",
    )
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"),
        "agent_events",
        "kind IN ('user_message', 'model_call', 'tool_call', "
        "'tool_result', 'assistant_message', 'timer')",
    )


def downgrade() -> None:
    # Timer events mean nothing to the older code, and its rule on event
    # kinds would refuse them.
    op.execute("DELETE FROM agent_events WHERE kind = 'timer'")
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"),
        "agent_events",
        "kind IN ('user_message', 'model_call', 'tool_call', "
        "'tool_result', 'assistant_message')",
    )
    op.drop_constraint(
        op.f("ck_sessions_paused_seconds_non_negative"), "sessions", type_="check"
    )
    op.drop_column("sessions", "paused_seconds")
    op.drop_column("sessions", "paused_at")
    op.drop_column("sessions", "turn_started_at")
    op.drop_column("sessions", "nudged_at")
    op.drop_column("sessions", "last_activity_at")
