"""create ordering and order tables

Revision ID: 41f5bd144077
Revises: 63f7e08598a6
Create Date: 2026-10-12 17:15:20.953830

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "41f5bd144077"
down_revision: str | Sequence[str] | None = "63f7e08598a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "lane", sa.String(), server_default=sa.text("'lane-1'"), nullable=False
        ),
        sa.Column(
            "state",
            sa.Enum(
                "greeting",
                "ordering",
                "confirming",
                "submitted",
                "handed_off",
                "abandoned",
                "terminated",
                name="sessionstate",
                native_enum=False,
                length=32,
            ),
            server_default=sa.text("'greeting'"),
            nullable=False,
        ),
        sa.Column(
            "language",
            sa.Enum("en", "fr", name="language", native_enum=False, length=32),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(), nullable=True),
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
            "(ended_at IS NULL) = (state IN ('confirming', 'greeting', 'ordering'))",
            name=op.f("ck_sessions_ended_at_matches_state"),
        ),
        sa.CheckConstraint(
            "language IN ('en', 'fr')", name=op.f("ck_sessions_language_valid")
        ),
        sa.CheckConstraint(
            "state IN ('greeting', 'ordering', 'confirming', "
            "'submitted', 'handed_off', 'abandoned', 'terminated')",
            name=op.f("ck_sessions_state_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(
        "uq_sessions_one_active_per_lane",
        "sessions",
        ["lane"],
        unique=True,
        postgresql_where=sa.text("state IN ('confirming', 'greeting', 'ordering')"),
    )
    op.create_table(
        "carts",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_line_no", sa.Integer(), server_default=sa.text("1"), nullable=False
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
            "next_line_no >= 1", name=op.f("ck_carts_next_line_no_positive")
        ),
        sa.CheckConstraint("version >= 0", name=op.f("ck_carts_version_non_negative")),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_carts_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carts")),
        sa.UniqueConstraint("session_id", name=op.f("uq_carts_session_id")),
    )
    op.create_table(
        "orders",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "order_number", sa.Integer(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "language",
            sa.Enum("en", "fr", name="language", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("tax_cents", sa.Integer(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
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
            "language IN ('en', 'fr')", name=op.f("ck_orders_language_valid")
        ),
        sa.CheckConstraint(
            "subtotal_cents >= 0", name=op.f("ck_orders_subtotal_cents_non_negative")
        ),
        sa.CheckConstraint(
            "tax_cents >= 0", name=op.f("ck_orders_tax_cents_non_negative")
        ),
        sa.CheckConstraint(
            "total_cents = subtotal_cents + tax_cents",
            name=op.f("ck_orders_total_matches"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_orders_session_id_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("order_number", name=op.f("uq_orders_order_number")),
        sa.UniqueConstraint("session_id", name=op.f("uq_orders_session_id")),
    )
    op.create_table(
        "cart_lines",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_cart_lines_line_no_positive")),
        sa.CheckConstraint(
            "quantity >= 1", name=op.f("ck_cart_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"],
            ["carts.id"],
            name=op.f("fk_cart_lines_cart_id_carts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_cart_lines_item_id_menu_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cart_lines")),
        sa.UniqueConstraint(
            "cart_id", "line_no", name=op.f("uq_cart_lines_cart_id_line_no")
        ),
    )
    op.create_index(
        op.f("ix_cart_lines_item_id"), "cart_lines", ["item_id"], unique=False
    )
    op.create_table(
        "order_lines",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("item_code", sa.String(), nullable=False),
        sa.Column("item_name", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
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
            "line_no >= 1", name=op.f("ck_order_lines_line_no_positive")
        ),
        sa.CheckConstraint(
            "line_total_cents = unit_price_cents * quantity",
            name=op.f("ck_order_lines_line_total_matches"),
        ),
        sa.CheckConstraint(
            "quantity >= 1", name=op.f("ck_order_lines_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name=op.f("ck_order_lines_unit_price_cents_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_order_lines_item_id_menu_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_lines_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_lines")),
        sa.UniqueConstraint(
            "order_id", "line_no", name=op.f("uq_order_lines_order_id_line_no")
        ),
    )
    op.create_index(
        op.f("ix_order_lines_item_id"), "order_lines", ["item_id"], unique=False
    )
    op.create_table(
        "order_line_options",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("order_line_id", sa.Uuid(), nullable=False),
        sa.Column("group_code", sa.String(), nullable=False),
        sa.Column("option_code", sa.String(), nullable=False),
        sa.Column("option_name", sa.String(), nullable=False),
        sa.Column("price_delta_cents", sa.Integer(), nullable=False),
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
            "price_delta_cents >= 0",
            name=op.f("ck_order_line_options_price_delta_cents_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_line_id"],
            ["order_lines.id"],
            name=op.f("fk_order_line_options_order_line_id_order_lines"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_line_options")),
        sa.UniqueConstraint(
            "order_line_id",
            "group_code",
            "option_code",
            name=op.f("uq_order_line_options_order_line_id_group_code_option_code"),
        ),
    )
    op.create_table(
        "cart_line_options",
        sa.Column("cart_line_id", sa.Uuid(), nullable=False),
        sa.Column("option_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cart_line_id"],
            ["cart_lines.id"],
            name=op.f("fk_cart_line_options_cart_line_id_cart_lines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["options.id"],
            name=op.f("fk_cart_line_options_option_id_options"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "cart_line_id", "option_id", name=op.f("pk_cart_line_options")
        ),
    )
    op.create_index(
        op.f("ix_cart_line_options_option_id"),
        "cart_line_options",
        ["option_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_cart_line_options_option_id"), table_name="cart_line_options"
    )
    op.drop_table("cart_line_options")
    op.drop_table("order_line_options")
    op.drop_index(op.f("ix_order_lines_item_id"), table_name="order_lines")
    op.drop_table("order_lines")
    op.drop_index(op.f("ix_cart_lines_item_id"), table_name="cart_lines")
    op.drop_table("cart_lines")
    op.drop_table("orders")
    op.drop_table("carts")
    op.drop_index(
        "uq_sessions_one_active_per_lane",
        table_name="sessions",
        postgresql_where=sa.text("state IN ('confirming', 'greeting', 'ordering')"),
    )
    op.drop_table("sessions")
