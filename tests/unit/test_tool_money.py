"""Amounts in tool results, written as money too. Pure logic, no database."""

import pytest

from drivetruagent.agent.tools import money, with_money


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(1690, "$16.90"), (5, "$0.05"), (0, "$0.00"), (200, "$2.00"), (-150, "-$1.50")],
)
def test_cents_are_written_as_dollars(cents: int, expected: str) -> None:
    assert money(cents) == expected


def test_every_amount_in_cents_gets_its_money_beside_it_at_any_depth() -> None:
    result = {
        "decision": "allow",
        "cart": {
            "total_cents": 1690,
            "lines": [{"item_name": "Fries", "unit_price_cents": 379}],
            "discounts": [{"name": "Burger combo", "amount_cents": 200}],
        },
    }

    assert with_money(result) == {
        "decision": "allow",
        "cart": {
            "total_cents": 1690,
            "total": "$16.90",
            "lines": [
                {"item_name": "Fries", "unit_price_cents": 379, "unit_price": "$3.79"}
            ],
            "discounts": [
                {"name": "Burger combo", "amount_cents": 200, "amount": "$2.00"}
            ],
        },
    }


def test_nothing_else_is_touched() -> None:
    result = {
        "total": "already money",
        "total_cents": 100,
        "on_hand": 4,
        "paid_cents": None,
        "flag_cents": True,
    }

    assert with_money(result) == result
