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

# Ontario HST, the app's default.
ONTARIO = Decimal("0.13")
# A rate with many decimal places, to exercise rounding.
MANY_DECIMALS = Decimal("0.14975")
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
        (997, ONTARIO, 130),  # 129.61 rounds up
        (1000, ONTARIO, 130),  # exactly 130
        (990, ONTARIO, 129),  # 128.70 rounds up
        (980, ONTARIO, 127),  # 127.40 rounds down
        (150, ONTARIO, 20),  # exactly 19.5 rounds up
        (50, ONTARIO, 7),  # exactly 6.5 rounds up, not to even
        (1000, MANY_DECIMALS, 150),  # 149.75 rounds up
        (997, MANY_DECIMALS, 149),  # 149.30 rounds down
        (200, MANY_DECIMALS, 30),  # 29.95 rounds up
        (10, FIVE_PERCENT, 1),  # exactly 0.5 rounds up, not to even
        (50, FIVE_PERCENT, 3),  # exactly 2.5 rounds up, not to even
        (30, FIVE_PERCENT, 2),  # exactly 1.5 rounds up
        (0, ONTARIO, 0),
        (1, ONTARIO, 0),  # 0.13 of a cent
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
    totals = cart_totals([758, 239], ONTARIO)

    assert totals == CartTotals(subtotal_cents=997, tax_cents=130, total_cents=1127)


def test_empty_cart_totals_are_zero() -> None:
    assert cart_totals([], ONTARIO) == CartTotals(0, 0, 0)


def test_tax_is_rounded_once_on_the_subtotal_not_per_line() -> None:
    """Three 10-cent lines at 5%: 1.5 cents of tax in total, which is 2.

    Rounding each line (0.5 -> 1 each) would charge 3.
    """
    assert cart_totals([10, 10, 10], FIVE_PERCENT).tax_cents == 2


def test_total_is_always_subtotal_plus_tax() -> None:
    for subtotal in range(0, 5000, 7):
        totals = cart_totals([subtotal], ONTARIO)

        assert totals.total_cents == totals.subtotal_cents + totals.tax_cents


# --- Discounts --------------------------------------------------------------


def test_tax_is_charged_on_what_is_left_after_the_discount() -> None:
    # A burger, small fries and a water (549 + 249 + 149), less a 200 combo.
    totals = cart_totals([549, 249, 149], ONTARIO, discount_cents=200)

    assert totals == CartTotals(
        subtotal_cents=947, discount_cents=200, tax_cents=97, total_cents=844
    )


def test_the_subtotal_is_the_sum_of_the_lines_whatever_the_discount() -> None:
    assert cart_totals([500, 500], ONTARIO, discount_cents=300).subtotal_cents == 1000


def test_no_discount_is_the_same_as_a_discount_of_zero() -> None:
    assert cart_totals([997], ONTARIO) == cart_totals([997], ONTARIO, 0)


def test_a_discount_never_takes_a_cart_below_zero() -> None:
    totals = cart_totals([300], ONTARIO, discount_cents=500)

    assert totals == CartTotals(
        subtotal_cents=300, discount_cents=300, tax_cents=0, total_cents=0
    )


def test_a_negative_discount_is_ignored() -> None:
    """A discount can only take money off. It can never add a charge."""
    assert cart_totals([300], ONTARIO, discount_cents=-50) == cart_totals(
        [300], ONTARIO
    )


def test_total_is_always_subtotal_less_discount_plus_tax() -> None:
    for subtotal in range(0, 5000, 7):
        for discount in (0, 1, 199, 200, 5000):
            totals = cart_totals([subtotal], ONTARIO, discount)

            assert totals.total_cents == (
                totals.subtotal_cents - totals.discount_cents + totals.tax_cents
            )
            assert 0 <= totals.discount_cents <= totals.subtotal_cents
