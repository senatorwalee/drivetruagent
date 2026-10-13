"""Which combos and specials a cart earns, and which combos it has started.

No database needed.
"""

from datetime import date

import pytest

from drivetruagent.enums import DiscountKind
from drivetruagent.ordering.discounts import (
    ComboRule,
    ComboSlot,
    Discount,
    Special,
    apply_combos,
    apply_specials,
    find_opening,
)


def rule(code: str, discount_cents: int, *categories: str) -> ComboRule:
    return ComboRule(
        code=code,
        name={"en": code.title()},
        discount_cents=discount_cents,
        slots=tuple(
            ComboSlot(category, {"en": category.title()}) for category in categories
        ),
    )


BURGER_COMBO = rule("burger_combo", 200, "burgers", "sides", "drinks")
SWEET_DEAL = rule("sweet_deal", 50, "drinks", "desserts")
DOUBLE_UP = rule("double_up", 300, "burgers", "burgers", "drinks")


def combo(code: str, times: int, amount_cents: int) -> Discount:
    return Discount(DiscountKind.COMBO, code, {"en": code.title()}, times, amount_cents)


def missing_from(
    leftover: dict[str, int], *rules: ComboRule, **options: object
) -> tuple[str, list[str]] | None:
    opening = find_opening(leftover, rules, **options)  # pyright: ignore[reportArgumentType]
    if opening is None:
        return None
    return opening.rule.code, [slot.category_code for slot in opening.missing]


# --- Applying ---------------------------------------------------------------


def test_one_of_each_makes_one_combo() -> None:
    applied = apply_combos({"burgers": 1, "sides": 1, "drinks": 1}, [BURGER_COMBO])

    assert applied.discounts == (combo("burger_combo", 1, 200),)
    assert applied.leftover == {"burgers": 0, "sides": 0, "drinks": 0}


def test_an_incomplete_set_earns_nothing() -> None:
    applied = apply_combos({"burgers": 1, "sides": 1}, [BURGER_COMBO])

    assert applied.discounts == ()
    assert applied.leftover == {"burgers": 1, "sides": 1}


def test_an_empty_cart_earns_nothing() -> None:
    assert apply_combos({}, [BURGER_COMBO]).discounts == ()


def test_no_rules_means_no_discounts() -> None:
    applied = apply_combos({"burgers": 3}, [])

    assert applied.discounts == ()
    assert applied.leftover == {"burgers": 3}


def test_two_complete_sets_make_two_combos() -> None:
    applied = apply_combos({"burgers": 2, "sides": 2, "drinks": 2}, [BURGER_COMBO])

    assert applied.discounts == (combo("burger_combo", 2, 400),)


def test_the_scarcest_part_decides_how_many() -> None:
    """Three burgers, one side: one combo, and two burgers left over."""
    applied = apply_combos({"burgers": 3, "sides": 1, "drinks": 5}, [BURGER_COMBO])

    assert applied.discounts == (combo("burger_combo", 1, 200),)
    assert applied.leftover == {"burgers": 2, "sides": 0, "drinks": 4}


def test_categories_no_rule_mentions_are_left_alone() -> None:
    applied = apply_combos(
        {"burgers": 1, "sides": 1, "drinks": 1, "desserts": 2}, [BURGER_COMBO]
    )

    assert applied.leftover["desserts"] == 2


def test_a_rule_can_need_two_of_one_category() -> None:
    assert apply_combos({"burgers": 2, "drinks": 1}, [DOUBLE_UP]).discounts == (
        combo("double_up", 1, 300),
    )
    assert apply_combos({"burgers": 1, "drinks": 1}, [DOUBLE_UP]).discounts == ()
    assert apply_combos({"burgers": 5, "drinks": 9}, [DOUBLE_UP]).discounts == (
        combo("double_up", 2, 600),
    )


def test_a_unit_counts_toward_one_combo_only() -> None:
    """One drink cannot complete both the burger combo and the sweet deal."""
    cart = {"burgers": 1, "sides": 1, "drinks": 1, "desserts": 1}

    applied = apply_combos(cart, [BURGER_COMBO, SWEET_DEAL])

    assert applied.discounts == (combo("burger_combo", 1, 200),)
    assert applied.leftover["desserts"] == 1


def test_rules_are_tried_in_the_order_given() -> None:
    cart = {"burgers": 1, "sides": 1, "drinks": 1, "desserts": 1}

    applied = apply_combos(cart, [SWEET_DEAL, BURGER_COMBO])

    assert applied.discounts == (combo("sweet_deal", 1, 50),)


def test_a_later_rule_uses_what_an_earlier_one_left() -> None:
    cart = {"burgers": 1, "sides": 1, "drinks": 2, "desserts": 1}

    applied = apply_combos(cart, [BURGER_COMBO, SWEET_DEAL])

    assert applied.discounts == (
        combo("burger_combo", 1, 200),
        combo("sweet_deal", 1, 50),
    )
    assert set(applied.leftover.values()) == {0}


def test_the_input_is_not_changed() -> None:
    cart = {"burgers": 1, "sides": 1, "drinks": 1}

    apply_combos(cart, [BURGER_COMBO])

    assert cart == {"burgers": 1, "sides": 1, "drinks": 1}


# --- Openings ---------------------------------------------------------------


def test_a_burger_alone_opens_the_combo() -> None:
    assert missing_from({"burgers": 1}, BURGER_COMBO) == (
        "burger_combo",
        ["sides", "drinks"],
    )


def test_a_burger_and_a_side_need_only_a_drink() -> None:
    assert missing_from({"burgers": 1, "sides": 1}, BURGER_COMBO) == (
        "burger_combo",
        ["drinks"],
    )


def test_without_the_main_item_nothing_is_open() -> None:
    """Nobody is asked to add a burger to their drink."""
    assert missing_from({"sides": 1, "drinks": 1}, BURGER_COMBO) is None
    assert missing_from({}, BURGER_COMBO) is None


def test_a_used_up_main_item_does_not_count() -> None:
    assert missing_from({"burgers": 0, "sides": 2}, BURGER_COMBO) is None


def test_leftovers_from_a_made_combo_can_open_another() -> None:
    leftover = apply_combos(
        {"burgers": 2, "sides": 1, "drinks": 1}, [BURGER_COMBO]
    ).leftover

    assert missing_from(dict(leftover), BURGER_COMBO) == (
        "burger_combo",
        ["sides", "drinks"],
    )


def test_a_rule_needing_two_of_a_category_counts_each_slot() -> None:
    assert missing_from({"burgers": 1}, DOUBLE_UP) == (
        "double_up",
        ["burgers", "drinks"],
    )
    assert missing_from({"burgers": 2}, DOUBLE_UP) == ("double_up", ["drinks"])


def test_the_first_open_rule_wins() -> None:
    leftover = {"burgers": 1, "drinks": 1}

    assert missing_from(leftover, SWEET_DEAL, BURGER_COMBO) == (
        "sweet_deal",
        ["desserts"],
    )
    assert missing_from(leftover, BURGER_COMBO, SWEET_DEAL) == (
        "burger_combo",
        ["sides"],
    )


def test_a_skipped_rule_is_passed_over() -> None:
    leftover = {"burgers": 1, "drinks": 1}

    assert missing_from(leftover, BURGER_COMBO, SWEET_DEAL, skip={"burger_combo"}) == (
        "sweet_deal",
        ["desserts"],
    )
    assert missing_from(leftover, BURGER_COMBO, skip={"burger_combo"}) is None


def test_a_combo_that_cannot_be_completed_is_not_open() -> None:
    """If every drink is sold out, there is no point offering the combo."""
    assert (
        missing_from({"burgers": 1}, BURGER_COMBO, sellable={"burgers", "sides"})
        is None
    )


def test_only_the_missing_parts_need_to_be_sellable() -> None:
    """The customer already has their side; sides selling out changes nothing."""
    assert missing_from(
        {"burgers": 1, "sides": 1}, BURGER_COMBO, sellable={"drinks"}
    ) == ("burger_combo", ["drinks"])


# --- Specials ---------------------------------------------------------------

MONDAY = date(2026, 10, 12)
ALL_WEEK = [date(2026, 10, day) for day in range(12, 19)]


def special(
    code: str = "shake_days",
    item_code: str = "milkshake",
    discount_cents: int = 100,
    base_price_cents: int = 399,
    weekdays: frozenset[int] = frozenset(),
) -> Special:
    return Special(
        code=code,
        name={"en": code.title()},
        description=None,
        item_code=item_code,
        item_name={"en": item_code.title()},
        base_price_cents=base_price_cents,
        discount_cents=discount_cents,
        weekdays=weekdays,
    )


def off(code: str, times: int, amount_cents: int) -> Discount:
    return Discount(
        DiscountKind.SPECIAL, code, {"en": code.title()}, times, amount_cents
    )


def test_the_twelfth_of_october_2026_is_a_monday() -> None:
    """The date these tests, and the eval scenarios, call Monday."""
    assert MONDAY.isoweekday() == 1


def test_a_special_runs_only_on_its_weekdays() -> None:
    weekdays_only = special(weekdays=frozenset({1, 2, 3, 4, 5}))

    assert [weekdays_only.runs_on(day) for day in ALL_WEEK] == [
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]


def test_a_special_with_no_weekdays_runs_every_day() -> None:
    assert all(special().runs_on(day) for day in ALL_WEEK)


def test_sunday_is_day_seven() -> None:
    sundays = special(weekdays=frozenset({7}))

    assert [day.isoweekday() for day in ALL_WEEK if sundays.runs_on(day)] == [7]


def test_the_special_price_is_the_base_price_less_the_saving() -> None:
    shake = special(discount_cents=100, base_price_cents=399)

    assert (shake.saves_cents, shake.special_price_cents) == (100, 299)


def test_a_special_never_takes_off_more_than_the_item_costs() -> None:
    """The item's price was lowered after the special was set up."""
    shake = special(discount_cents=500, base_price_cents=399)

    assert (shake.saves_cents, shake.special_price_cents) == (399, 0)


def test_every_unit_of_the_item_gets_the_special() -> None:
    assert apply_specials({"milkshake": 3, "fries": 2}, [special()]) == (
        off("shake_days", 3, 300),
    )


def test_a_cart_without_the_item_gets_nothing() -> None:
    assert apply_specials({"fries": 2}, [special()]) == ()
    assert apply_specials({}, [special()]) == ()
    assert apply_specials({"milkshake": 0}, [special()]) == ()


def test_no_specials_means_no_discounts() -> None:
    assert apply_specials({"milkshake": 3}, []) == ()


def test_specials_on_different_items_both_apply() -> None:
    specials = [special(), special("pie_days", "apple_pie", 50, 199)]

    assert apply_specials({"milkshake": 1, "apple_pie": 2}, specials) == (
        off("shake_days", 1, 100),
        off("pie_days", 2, 100),
    )


def test_two_specials_on_one_item_do_not_stack() -> None:
    """The first in priority order applies; the second is ignored."""
    specials = [
        special("shake_days", discount_cents=100),
        special("shake_more", discount_cents=150),
    ]

    assert apply_specials({"milkshake": 2}, specials) == (off("shake_days", 2, 200),)
    assert apply_specials({"milkshake": 2}, specials[::-1]) == (
        off("shake_more", 2, 300),
    )


def test_the_amount_uses_the_capped_saving() -> None:
    capped = special(discount_cents=500, base_price_cents=399)

    assert apply_specials({"milkshake": 2}, [capped]) == (off("shake_days", 2, 798),)


@pytest.mark.parametrize("units", [1, 2, 7])
def test_the_amount_is_the_saving_times_the_units(units: int) -> None:
    (discount,) = apply_specials({"milkshake": units}, [special()])

    assert (discount.times, discount.amount_cents) == (units, 100 * units)
