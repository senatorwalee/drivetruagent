"""Which combos a cart earns, and which it has started. No database needed."""

from drivetruagent.enums import DiscountKind
from drivetruagent.ordering.discounts import (
    ComboRule,
    ComboSlot,
    Discount,
    apply_combos,
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
