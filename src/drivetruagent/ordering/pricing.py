"""Prices. Pure logic: no database.

All money is integer cents. Tax is the only place a fraction appears, and it
is rounded exactly once, on the cart subtotal, so that subtotal + tax = total
always holds to the cent.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class CartTotals:
    subtotal_cents: int
    tax_cents: int
    total_cents: int


def unit_price_cents(base_price_cents: int, option_deltas_cents: Iterable[int]) -> int:
    """The price of one of an item: its base price plus each chosen option."""
    return base_price_cents + sum(option_deltas_cents)


def line_total_cents(unit_price: int, quantity: int) -> int:
    return unit_price * quantity


def tax_cents(subtotal_cents: int, tax_rate: Decimal) -> int:
    """Tax on a subtotal, rounded half up to the nearest cent.

    Decimal, not float: 0.14975 has no exact float form, and a price that is
    off by a cent now and then is a bug customers notice.
    """
    exact = Decimal(subtotal_cents) * tax_rate
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cart_totals(line_totals_cents: Iterable[int], tax_rate: Decimal) -> CartTotals:
    subtotal = sum(line_totals_cents)
    tax = tax_cents(subtotal, tax_rate)
    return CartTotals(
        subtotal_cents=subtotal, tax_cents=tax, total_cents=subtotal + tax
    )
