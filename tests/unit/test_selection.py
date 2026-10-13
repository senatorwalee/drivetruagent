"""Which options a line ends up with. Pure logic, no database."""

import uuid

import pytest

from drivetruagent.ordering.errors import (
    MissingRequiredOptions,
    OptionUnavailable,
    TooFewOptions,
    TooManyOptions,
    UnknownOption,
    UnknownOptionGroup,
)
from drivetruagent.ordering.selection import (
    GroupSpec,
    ItemSpec,
    OptionSpec,
    Selections,
    merge_selections,
    resolve_selection,
)


def option(
    code: str, delta: int = 0, *, default: bool = False, active: bool = True
) -> OptionSpec:
    return OptionSpec(
        id=uuid.uuid4(),
        code=code,
        name={"en": code.title(), "fr": code.title()},
        price_delta_cents=delta,
        is_default=default,
        is_active=active,
    )


def group(
    code: str, min_select: int, max_select: int, *options: OptionSpec
) -> GroupSpec:
    return GroupSpec(
        code=code,
        name={"en": code.title(), "fr": code.title()},
        min_select=min_select,
        max_select=max_select,
        options=options,
    )


def item(code: str, *groups: GroupSpec) -> ItemSpec:
    return ItemSpec(
        id=uuid.uuid4(),
        code=code,
        name={"en": code.title(), "fr": code.title()},
        category_code="sides",
        base_price_cents=249,
        is_active=True,
        groups=groups,
    )


# Size must be asked for; style and seasoning have defaults.
FRIES = item(
    "fries",
    group("size", 1, 1, option("small"), option("medium", 70), option("large", 130)),
    group(
        "style",
        1,
        1,
        option("regular", default=True),
        option("curly", 50),
        option("waffle", 50),
    ),
    group("seasoning", 1, 1, option("plain", default=True), option("seasoned")),
)

# Count must be asked for; up to two dips are optional.
NUGGETS = item(
    "chicken_nuggets",
    group("count", 1, 1, option("6_pieces"), option("9_pieces", 200)),
    group("dip", 0, 2, option("ketchup"), option("barbecue"), option("sweet_chili")),
)

# Two required choices and no defaults.
MILKSHAKE = item(
    "milkshake",
    group("size", 1, 1, option("small"), option("large", 120)),
    group("flavour", 1, 1, option("vanilla"), option("chocolate")),
)

COOKIE = item("cookie")


def codes(item_spec: ItemSpec, requested: Selections) -> dict[str, list[str]]:
    """Resolve and return {group: [options]} for easy comparison."""
    result: dict[str, list[str]] = {}
    for choice in resolve_selection(item_spec, requested):
        result.setdefault(choice.group.code, []).append(choice.option.code)
    return result


# --- Defaults and required choices ------------------------------------------


def test_item_without_groups_needs_nothing() -> None:
    assert resolve_selection(COOKIE, {}) == []


def test_defaults_are_filled_in() -> None:
    assert codes(FRIES, {"size": ["large"]}) == {
        "size": ["large"],
        "style": ["regular"],
        "seasoning": ["plain"],
    }


def test_explicit_choice_overrides_the_default() -> None:
    assert codes(FRIES, {"size": ["small"], "style": ["curly"]})["style"] == ["curly"]


def test_required_group_without_a_default_is_reported_as_missing() -> None:
    with pytest.raises(MissingRequiredOptions) as error:
        resolve_selection(FRIES, {})

    missing = error.value.details["missing"]
    assert [group["group_code"] for group in missing] == ["size"]
    assert [option["option_code"] for option in missing[0]["options"]] == [
        "small",
        "medium",
        "large",
    ]


def test_every_missing_group_is_reported_at_once() -> None:
    with pytest.raises(MissingRequiredOptions) as error:
        resolve_selection(MILKSHAKE, {})

    missing = error.value.details["missing"]
    assert [group["group_code"] for group in missing] == ["size", "flavour"]


def test_only_the_groups_still_missing_are_reported() -> None:
    with pytest.raises(MissingRequiredOptions) as error:
        resolve_selection(MILKSHAKE, {"size": ["large"]})

    missing = error.value.details["missing"]
    assert [group["group_code"] for group in missing] == ["flavour"]


def test_missing_group_details_include_prices() -> None:
    with pytest.raises(MissingRequiredOptions) as error:
        resolve_selection(FRIES, {})

    large = error.value.details["missing"][0]["options"][2]
    assert large == {
        "option_code": "large",
        "option_name": {"en": "Large", "fr": "Large"},
        "price_delta_cents": 130,
    }


def test_empty_list_for_a_required_group_falls_back_to_the_default() -> None:
    assert codes(FRIES, {"size": ["small"], "style": []})["style"] == ["regular"]


def test_empty_list_for_a_required_group_without_default_is_missing() -> None:
    with pytest.raises(MissingRequiredOptions):
        resolve_selection(FRIES, {"size": []})


# --- Optional groups --------------------------------------------------------


def test_optional_group_may_be_left_empty() -> None:
    assert codes(NUGGETS, {"count": ["6_pieces"]}) == {"count": ["6_pieces"]}


def test_optional_group_accepts_up_to_its_maximum() -> None:
    result = codes(NUGGETS, {"count": ["6_pieces"], "dip": ["ketchup", "barbecue"]})

    assert result["dip"] == ["ketchup", "barbecue"]


def test_choosing_more_than_the_maximum_is_rejected() -> None:
    with pytest.raises(TooManyOptions) as error:
        resolve_selection(
            NUGGETS,
            {"count": ["6_pieces"], "dip": ["ketchup", "barbecue", "sweet_chili"]},
        )

    assert error.value.details["group_code"] == "dip"
    assert error.value.details["max_select"] == 2


def test_two_choices_for_a_pick_one_group_are_rejected() -> None:
    with pytest.raises(TooManyOptions):
        resolve_selection(FRIES, {"size": ["small", "large"]})


def test_choosing_fewer_than_the_minimum_is_rejected() -> None:
    pizza = item(
        "pizza", group("toppings", 2, 3, option("ham"), option("olive"), option("corn"))
    )

    with pytest.raises(TooFewOptions) as error:
        resolve_selection(pizza, {"toppings": ["ham"]})

    assert error.value.details["min_select"] == 2


# --- Things the menu does not have ------------------------------------------


def test_unknown_group_is_rejected() -> None:
    with pytest.raises(UnknownOptionGroup) as error:
        resolve_selection(FRIES, {"size": ["small"], "temperature": ["hot"]})

    assert error.value.details["valid_groups"] == ["size", "style", "seasoning"]


def test_group_on_an_item_without_groups_is_rejected() -> None:
    with pytest.raises(UnknownOptionGroup):
        resolve_selection(COOKIE, {"size": ["large"]})


def test_unknown_option_is_rejected_with_the_valid_ones() -> None:
    with pytest.raises(UnknownOption) as error:
        resolve_selection(FRIES, {"size": ["huge"]})

    assert error.value.details["valid_options"] == ["small", "medium", "large"]


def test_inactive_option_is_rejected() -> None:
    cola = item(
        "cola", group("size", 1, 1, option("small"), option("large", active=False))
    )

    with pytest.raises(OptionUnavailable) as error:
        resolve_selection(cola, {"size": ["large"]})

    # Only what can actually be offered instead.
    assert error.value.details["valid_options"] == ["small"]


def test_inactive_default_is_not_filled_in() -> None:
    cola = item(
        "cola",
        group(
            "ice", 1, 1, option("regular", default=True, active=False), option("none")
        ),
    )

    with pytest.raises(MissingRequiredOptions) as error:
        resolve_selection(cola, {})

    offered = error.value.details["missing"][0]["options"]
    assert [option["option_code"] for option in offered] == ["none"]


# --- Tolerance --------------------------------------------------------------


def test_repeated_option_is_collapsed() -> None:
    assert codes(FRIES, {"size": ["large", "large"]})["size"] == ["large"]


def test_repeats_do_not_count_towards_the_maximum() -> None:
    result = codes(NUGGETS, {"count": ["6_pieces"], "dip": ["ketchup", "ketchup"]})

    assert result["dip"] == ["ketchup"]


def test_result_follows_menu_order_not_request_order() -> None:
    result = resolve_selection(FRIES, {"seasoning": ["seasoned"], "size": ["small"]})

    assert [choice.group.code for choice in result] == ["size", "style", "seasoning"]


# --- Merging a change into existing choices ---------------------------------


def test_merge_replaces_only_the_named_group() -> None:
    current = {"size": ["small"], "style": ["curly"]}

    assert merge_selections(current, {"size": ["large"]}) == {
        "size": ["large"],
        "style": ["curly"],
    }


def test_merge_with_an_empty_list_clears_the_group() -> None:
    current = {"count": ["6_pieces"], "dip": ["ketchup"]}

    assert merge_selections(current, {"dip": []}) == {"count": ["6_pieces"], "dip": []}


def test_merge_can_add_a_group_that_was_empty() -> None:
    assert merge_selections({"count": ["6_pieces"]}, {"dip": ["barbecue"]}) == {
        "count": ["6_pieces"],
        "dip": ["barbecue"],
    }


def test_merge_does_not_change_its_inputs() -> None:
    current = {"size": ["small"]}

    merge_selections(current, {"size": ["large"]})

    assert current == {"size": ["small"]}
