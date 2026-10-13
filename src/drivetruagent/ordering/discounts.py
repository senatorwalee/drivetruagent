"""What comes off a cart, and what could. Pure logic: no database.

A discount is never asked for. It is worked out from what is in the cart,
every time the cart is priced, the same way tax is. Take the drink back out
and the combo is simply not there the next time.
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from drivetruagent.enums import DiscountKind


@dataclass(frozen=True)
class ComboSlot:
    """One unit a combo needs: any single item from this category."""

    category_code: str
    category_name: Mapping[str, str]


@dataclass(frozen=True)
class ComboRule:
    code: str
    name: Mapping[str, str]
    # Taken off once for every complete set.
    discount_cents: int
    # The first slot is the main item, the one the combo is built around.
    slots: tuple[ComboSlot, ...]

    @property
    def needs(self) -> Counter[str]:
        """How many units of each category one set takes."""
        return Counter(slot.category_code for slot in self.slots)

    @property
    def main_category(self) -> str:
        return self.slots[0].category_code


@dataclass(frozen=True)
class Discount:
    kind: DiscountKind
    code: str
    name: Mapping[str, str]
    # How many times it applied.
    times: int
    # The whole amount, across every time it applied.
    amount_cents: int


@dataclass(frozen=True)
class CombosApplied:
    discounts: tuple[Discount, ...]
    # Units in each category that are not part of any combo.
    leftover: Mapping[str, int]


@dataclass(frozen=True)
class ComboOpening:
    """A combo the cart has started but not completed."""

    rule: ComboRule
    # The slots still to fill, in the rule's own order.
    missing: tuple[ComboSlot, ...]


def apply_combos(
    units_by_category: Mapping[str, int], rules: Sequence[ComboRule]
) -> CombosApplied:
    """Make as many combos as the cart's units allow.

    Rules are tried in the order given, which is their priority. Each unit
    counts toward one combo at most: two burgers, one side and one drink make
    one combo, not two. Going in priority order is not always the largest
    saving possible when rules overlap, but it is predictable, and it is the
    answer a customer can be given a reason for.
    """
    leftover = {category: units for category, units in units_by_category.items()}
    discounts: list[Discount] = []
    for rule in rules:
        times = min(
            leftover.get(category, 0) // needed
            for category, needed in rule.needs.items()
        )
        if times == 0:
            continue
        for category, needed in rule.needs.items():
            leftover[category] -= needed * times
        discounts.append(
            Discount(
                kind=DiscountKind.COMBO,
                code=rule.code,
                name=rule.name,
                times=times,
                amount_cents=rule.discount_cents * times,
            )
        )
    return CombosApplied(tuple(discounts), leftover)


def find_opening(
    leftover: Mapping[str, int],
    rules: Sequence[ComboRule],
    *,
    skip: Collection[str] = (),
    sellable: Collection[str] | None = None,
) -> ComboOpening | None:
    """The first combo that the leftover units have started.

    A combo is open when its main item is there and something else is not. A
    drink on its own opens nothing: nobody is asked to add a burger to their
    drink.

    `skip` holds rule codes not to suggest. `sellable` holds the categories
    that have something in stock; a combo that needs anything else cannot be
    completed, so it is not suggested. None means everything is sellable.
    """
    for rule in rules:
        if rule.code in skip or leftover.get(rule.main_category, 0) < 1:
            continue
        missing = _missing_slots(rule, leftover)
        if not missing:
            continue
        if sellable is not None and any(
            slot.category_code not in sellable for slot in missing
        ):
            continue
        return ComboOpening(rule, missing)
    return None


def _missing_slots(
    rule: ComboRule, leftover: Mapping[str, int]
) -> tuple[ComboSlot, ...]:
    available = Counter({c: n for c, n in leftover.items() if n > 0})
    missing: list[ComboSlot] = []
    for slot in rule.slots:
        if available[slot.category_code] > 0:
            available[slot.category_code] -= 1
        else:
            missing.append(slot)
    return tuple(missing)
