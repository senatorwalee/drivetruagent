"""add guardrail strikes and the guardrail event kind

Revision ID: df3652adf06d
Revises: 4275d30f1796
Create Date: 2026-10-14 11:05:47.778954

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "df3652adf06d"
down_revision: str | Sequence[str] | None = "4275d30f1796"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "abuse_strikes", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "confusion_strikes",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    # Autogenerate does not detect CHECK constraints; these are written by hand.
    for column in ("abuse_strikes", "confusion_strikes"):
        op.create_check_constraint(
            op.f(f"ck_sessions_{column}_non_negative"), "sessions", f"{column} >= 0"
        )
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"),
        "agent_events",
        "kind IN ('user_message', 'model_call', 'tool_call', "
        "'tool_result', 'assistant_message', 'timer', 'guardrail')",
    )


def downgrade() -> None:
    # Guardrail events mean nothing to the older code, and its rule on event
    # kinds would refuse them.
    op.execute("DELETE FROM agent_events WHERE kind = 'guardrail'")
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"),
        "agent_events",
        "kind IN ('user_message', 'model_call', 'tool_call', "
        "'tool_result', 'assistant_message', 'timer')",
    )
    for column in ("abuse_strikes", "confusion_strikes"):
        op.drop_constraint(
            op.f(f"ck_sessions_{column}_non_negative"), "sessions", type_="check"
        )
    op.drop_column("sessions", "confusion_strikes")
    op.drop_column("sessions", "abuse_strikes")
