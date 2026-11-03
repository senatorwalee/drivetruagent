"""add the noise event kind

Revision ID: c0ddba83c29d
Revises: a7d3c1f09e52
Create Date: 2026-11-02 18:59:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0ddba83c29d"
down_revision: str | Sequence[str] | None = "a7d3c1f09e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'user_message', 'model_call', 'tool_call', "
    "'tool_result', 'assistant_message', 'timer', 'guardrail'"
)


def upgrade() -> None:
    # Autogenerate does not detect CHECK constraints; this is written by hand.
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"),
        "agent_events",
        f"kind IN ({KINDS}, 'noise')",
    )


def downgrade() -> None:
    # Noise events mean nothing to the older code, and its rule on event
    # kinds would refuse them.
    op.execute("DELETE FROM agent_events WHERE kind = 'noise'")
    op.drop_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_agent_events_kind_valid"), "agent_events", f"kind IN ({KINDS})"
    )
