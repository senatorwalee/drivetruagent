"""create combo rules, order discounts and session suggestions

Revision ID: bf474abf4b65
Revises: 3e43863cc921
Create Date: 2026-10-13 04:57:00.206753

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "bf474abf4b65"
down_revision: str | Sequence[str] | None = "3e43863cc921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "combo_rules",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column(
            "name_i18n",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
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
            "name_i18n IS NULL "
            "OR jsonb_typeof(name_i18n -> 'en') IS NOT DISTINCT FROM 'string'",
            name=op.f("ck_combo_rules_name_i18n_has_en"),
        ),
        sa.CheckConstraint(
            "discount_cents > 0", name=op.f("ck_combo_rules_discount_cents_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_combo_rules")),
        sa.UniqueConstraint("code", name=op.f("uq_combo_rules_code")),
    )
    op.create_table(
        "combo_rule_slots",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("combo_rule_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
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
            "position >= 0", name=op.f("ck_combo_rule_slots_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["menu_categories.id"],
            name=op.f("fk_combo_rule_slots_category_id_menu_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["combo_rule_id"],
            ["combo_rules.id"],
            name=op.f("fk_combo_rule_slots_combo_rule_id_combo_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_combo_rule_slots")),
        sa.UniqueConstraint(
            "combo_rule_id",
            "position",
            name=op.f("uq_combo_rule_slots_combo_rule_id_position"),
        ),
    )
    op.create_index(
        op.f("ix_combo_rule_slots_category_id"),
        "combo_rule_slots",
        ["category_id"],
        unique=False,
    )
    op.create_table(
        "session_suggestions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "combo", "special", name="discountkind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open", "closed", name="suggestionstatus", native_enum=False, length=32
            ),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
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
            "kind IN ('combo', 'special')",
            name=op.f("ck_session_suggestions_kind_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed')",
            name=op.f("ck_session_suggestions_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_suggestions_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_suggestions")),
        sa.UniqueConstraint(
            "session_id",
            "kind",
            "code",
            name=op.f("uq_session_suggestions_session_id_kind_code"),
        ),
    )
    op.create_table(
        "order_discounts",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "combo", "special", name="discountkind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("times", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
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
            "kind IN ('combo', 'special')", name=op.f("ck_order_discounts_kind_valid")
        ),
        sa.CheckConstraint(
            "amount_cents >= 0",
            name=op.f("ck_order_discounts_amount_cents_non_negative"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_order_discounts_position_non_negative")
        ),
        sa.CheckConstraint(
            "times >= 1", name=op.f("ck_order_discounts_times_positive")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_discounts_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_discounts")),
        sa.UniqueConstraint(
            "order_id", "position", name=op.f("uq_order_discounts_order_id_position")
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "discount_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    # Autogenerate does not detect CHECK constraints; these are written by hand.
    # Existing orders have no discount, so they satisfy the new rule as they are.
    op.create_check_constraint(
        op.f("ck_orders_discount_cents_non_negative"), "orders", "discount_cents >= 0"
    )
    op.create_check_constraint(
        op.f("ck_orders_discount_within_subtotal"),
        "orders",
        "discount_cents <= subtotal_cents",
    )
    op.drop_constraint(op.f("ck_orders_total_matches"), "orders", type_="check")
    op.create_check_constraint(
        op.f("ck_orders_total_matches"),
        "orders",
        "total_cents = subtotal_cents - discount_cents + tax_cents",
    )


def downgrade() -> None:
    # Orders that carry a discount cannot satisfy the old total rule. Going
    # back fails on purpose if any exist, instead of silently changing totals.
    op.drop_constraint(op.f("ck_orders_total_matches"), "orders", type_="check")
    op.create_check_constraint(
        op.f("ck_orders_total_matches"),
        "orders",
        "total_cents = subtotal_cents + tax_cents",
    )
    op.drop_constraint(
        op.f("ck_orders_discount_within_subtotal"), "orders", type_="check"
    )
    op.drop_constraint(
        op.f("ck_orders_discount_cents_non_negative"), "orders", type_="check"
    )
    op.drop_column("orders", "discount_cents")
    op.drop_table("order_discounts")
    op.drop_table("session_suggestions")
    op.drop_index(
        op.f("ix_combo_rule_slots_category_id"), table_name="combo_rule_slots"
    )
    op.drop_table("combo_rule_slots")
    op.drop_table("combo_rules")
