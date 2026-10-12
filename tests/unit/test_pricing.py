"""Prices and tax. Pure logic, no database."""

from decimal import Decimal

import pytest

from drivetruagent.ordering.pricing import (
    CartTotals,
    cart_totals,
    line_total_cents,
    tax_cents,
    unit_price_cents,
)

QUEBEC = Decimal("0.14975")
FIVE_PERCENT = Decimal("0.05")


def test_unit_price_is_base_plus_options() -> None:
    # Large curly fries: 249 base, +130 for large, +50 for curly.
    assert unit_price_cents(249, [130, 50, 0]) == 429


def test_unit_price_without_options_is_the_base_price() -> None:
    assert unit_price_cents(149, []) == 149


def test_line_total_is_unit_price_times_quantity() -> None:
    assert line_total_cents(429, 3) == 1287


@pytest.mark.parametrize(
    ("subtotal", "rate", "expected"),
    [
        (1000, QUEBEC, 150),  # 149.75 rounds up
        (997, QUEBEC, 149),  # 149.30 rounds down
        (200, QUEBEC, 30),  # 29.95 rounds up
        (10, FIVE_PERCENT, 1),  # exactly 0.5 rounds up, not to even
        (50, FIVE_PERCENT, 3),  # exactly 2.5 rounds up, not to even
        (30, FIVE_PERCENT, 2),  # exactly 1.5 rounds up
        (0, QUEBEC, 0),
        (1, QUEBEC, 0),  # 0.15 of a cent
    ],
)
def test_tax_is_rounded_half_up_to_the_cent(
    subtotal: int, rate: Decimal, expected: int
) -> None:
    assert tax_cents(subtotal, rate) == expected


def test_zero_tax_rate_gives_no_tax() -> None:
    assert tax_cents(12345, Decimal(0)) == 0


def test_cart_totals_add_up() -> None:
    # Two large fries (2 x 379) and a medium cola (239).
    totals = cart_totals([758, 239], QUEBEC)

    assert totals == CartTotals(subtotal_cents=997, tax_cents=149, total_cents=1146)


def test_empty_cart_totals_are_zero() -> None:
    assert cart_totals([], QUEBEC) == CartTotals(0, 0, 0)


def test_tax_is_rounded_once_on_the_subtotal_not_per_line() -> None:
    """Three 10-cent lines at 5%: 1.5 cents of tax in total, which is 2.

    Rounding each line (0.5 -> 1 each) would charge 3.
    """
    assert cart_totals([10, 10, 10], FIVE_PERCENT).tax_cents == 2


def test_total_is_always_subtotal_plus_tax() -> None:
    for subtotal in range(0, 5000, 7):
        totals = cart_totals([subtotal], QUEBEC)

        assert totals.total_cents == totals.subtotal_cents + totals.tax_cents
