"""Validation of the menu data file. No database needed."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from drivetruagent.enums import Language
from drivetruagent.seed import MenuSeed, OptionGroupSeed, load_menu

MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "menu.json"


def name(en: str = "Fries", fr: str = "Frites") -> dict[str, str]:
    return {"en": en, "fr": fr}


def option(code: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "name": name(), **extra}


def group(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": "size",
        "name": name("Size", "Format"),
        "min_select": 1,
        "max_select": 1,
        "options": [option("small"), option("large")],
    }
    return values | overrides


def item(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": "fries",
        "name": name(),
        "base_price_cents": 249,
    }
    return values | overrides


def menu(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "categories": [
            {"code": "sides", "name": name("Sides", "Accompagnements"), "items": items}
        ]
    }


# --- The real menu file -----------------------------------------------------


def test_real_menu_file_is_valid() -> None:
    loaded = load_menu(MENU_PATH)

    items = [item for category in loaded.categories for item in category.items]
    assert [category.code for category in loaded.categories] == [
        "burgers",
        "sides",
        "drinks",
        "desserts",
    ]
    assert len(items) == 20


def test_fries_ask_only_for_size() -> None:
    """Size has no default, so the agent must ask; style and seasoning default."""
    loaded = load_menu(MENU_PATH)
    fries = next(
        item
        for category in loaded.categories
        for item in category.items
        if item.code == "fries"
    )

    must_ask = [
        group.code
        for group in fries.option_groups
        if group.min_select >= 1 and not any(o.default for o in group.options)
    ]
    defaults = {
        group.code: next(o.code for o in group.options if o.default)
        for group in fries.option_groups
        if any(o.default for o in group.options)
    }

    assert must_ask == ["size"]
    assert defaults == {"style": "regular", "seasoning": "plain"}


def test_every_item_has_an_alias_in_each_language() -> None:
    loaded = load_menu(MENU_PATH)

    for category in loaded.categories:
        for menu_item in category.items:
            for language in Language:
                assert menu_item.aliases.get(language), (menu_item.code, language)


# --- Mistakes the file check must catch -------------------------------------


def test_misspelt_field_name_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(menu(item(base_price_cent=249))))

    with pytest.raises(ValidationError, match="base_price_cent"):
        load_menu(path)


def test_missing_french_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="fr"):
        MenuSeed.model_validate(menu(item(name={"en": "Fries"})))


def test_empty_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MenuSeed.model_validate(menu(item(name=name(fr=""))))


def test_negative_price_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MenuSeed.model_validate(menu(item(base_price_cents=-1)))


def test_code_with_spaces_or_capitals_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MenuSeed.model_validate(menu(item(code="French Fries")))


def test_duplicate_item_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate item code: fries"):
        MenuSeed.model_validate(menu(item(), item()))


def test_duplicate_group_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate group code: size"):
        MenuSeed.model_validate(menu(item(option_groups=[group(), group()])))


def test_duplicate_option_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate option code: small"):
        OptionGroupSeed.model_validate(
            group(options=[option("small"), option("small")])
        )


def test_two_default_options_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at most one default"):
        OptionGroupSeed.model_validate(
            group(
                options=[option("small", default=True), option("large", default=True)]
            )
        )


def test_default_on_an_optional_group_is_rejected() -> None:
    with pytest.raises(ValidationError, match="default option needs"):
        OptionGroupSeed.model_validate(
            group(
                min_select=0,
                options=[option("small", default=True), option("large")],
            )
        )


def test_max_select_below_min_select_is_rejected() -> None:
    with pytest.raises(ValidationError, match="max_select must be at least"):
        OptionGroupSeed.model_validate(group(min_select=2, max_select=1))


def test_max_select_above_number_of_options_is_rejected() -> None:
    with pytest.raises(ValidationError, match="larger than the number of options"):
        OptionGroupSeed.model_validate(group(min_select=0, max_select=3))


def test_group_without_options_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OptionGroupSeed.model_validate(group(options=[]))


# --- Aliases ----------------------------------------------------------------


def test_aliases_are_lowercased_and_trimmed() -> None:
    loaded = MenuSeed.model_validate(menu(item(aliases={"en": ["  Chips ", "FRY"]})))

    assert loaded.categories[0].items[0].aliases == {Language.EN: ["chips", "fry"]}


def test_aliases_that_collide_after_normalising_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate en alias: chips"):
        MenuSeed.model_validate(menu(item(aliases={"en": ["chips", "Chips "]})))


def test_alias_in_an_unsupported_language_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MenuSeed.model_validate(menu(item(aliases={"de": ["pommes"]})))


def test_blank_alias_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        MenuSeed.model_validate(menu(item(aliases={"en": ["  "]})))


# --- Combos -----------------------------------------------------------------


def combo(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": "burger_combo",
        "name": name("Burger combo", "Trio burger"),
        "discount_cents": 200,
        "slots": ["burgers", "sides"],
    }
    return values | overrides


def menu_with_combos(*combos: dict[str, Any]) -> dict[str, Any]:
    """A burger at 549 and fries at 249, so the cheapest set costs 798."""
    return {
        "categories": [
            {
                "code": "burgers",
                "name": name("Burgers", "Hamburgers"),
                "items": [item(code="burger", base_price_cents=549)],
            },
            {
                "code": "sides",
                "name": name("Sides", "Accompagnements"),
                "items": [item(), item(code="rings", base_price_cents=329)],
            },
            {"code": "drinks", "name": name("Drinks", "Boissons"), "items": []},
        ],
        "combos": list(combos),
    }


def test_the_real_menu_has_the_burger_combo() -> None:
    (burger_combo,) = load_menu(MENU_PATH).combos

    assert burger_combo.code == "burger_combo"
    assert burger_combo.slots == ["burgers", "sides", "drinks"]
    assert burger_combo.discount_cents == 200


def test_a_menu_needs_no_combos() -> None:
    assert MenuSeed.model_validate(menu(item())).combos == []


def test_a_valid_combo_is_accepted() -> None:
    loaded = MenuSeed.model_validate(menu_with_combos(combo()))

    assert [each.code for each in loaded.combos] == ["burger_combo"]


def test_a_combo_on_an_unknown_category_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no items in category pizzas"):
        MenuSeed.model_validate(menu_with_combos(combo(slots=["burgers", "pizzas"])))


def test_a_combo_on_a_category_with_no_items_is_rejected() -> None:
    """Nobody could ever complete it."""
    with pytest.raises(ValidationError, match="no items in category drinks"):
        MenuSeed.model_validate(menu_with_combos(combo(slots=["burgers", "drinks"])))


def test_a_combo_of_one_thing_is_rejected() -> None:
    with pytest.raises(ValidationError, match="slots"):
        MenuSeed.model_validate(menu_with_combos(combo(slots=["burgers"])))


@pytest.mark.parametrize("discount_cents", [0, -100])
def test_a_combo_must_take_something_off(discount_cents: int) -> None:
    with pytest.raises(ValidationError, match="discount_cents"):
        MenuSeed.model_validate(menu_with_combos(combo(discount_cents=discount_cents)))


def test_a_combo_cannot_make_its_cheapest_set_free() -> None:
    with pytest.raises(ValidationError, match="not less than its cheapest set"):
        MenuSeed.model_validate(menu_with_combos(combo(discount_cents=798)))

    MenuSeed.model_validate(menu_with_combos(combo(discount_cents=797)))


def test_a_slot_used_twice_is_priced_twice() -> None:
    """Two burgers: the cheapest set is 2 x 549."""
    twice = ["burgers", "burgers"]

    MenuSeed.model_validate(menu_with_combos(combo(slots=twice, discount_cents=1097)))
    with pytest.raises(ValidationError, match="not less than its cheapest set"):
        MenuSeed.model_validate(
            menu_with_combos(combo(slots=twice, discount_cents=1098))
        )


def test_two_combos_with_one_code_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate combo code"):
        MenuSeed.model_validate(menu_with_combos(combo(), combo()))
