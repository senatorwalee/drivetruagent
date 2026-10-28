"""add the not suitable diet status

Revision ID: 03620e2eae11
Revises: c45119a7dd0f
Create Date: 2026-10-28 11:30:21.334539

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03620e2eae11"
down_revision: str | Sequence[str] | None = "c45119a7dd0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A text column limited by a check constraint (see text_enum in db/base.py),
# so a new status is a constraint change.
NAME = "ck_diet_suitability_status_valid"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), "diet_suitability", type_="check")
    op.create_check_constraint(
        op.f(NAME),
        "diet_suitability",
        "status IN ('certified', 'suitable', 'not_suitable')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM diet_suitability WHERE status = 'not_suitable'")
    op.drop_constraint(op.f(NAME), "diet_suitability", type_="check")
    op.create_check_constraint(
        op.f(NAME), "diet_suitability", "status IN ('certified', 'suitable')"
    )
