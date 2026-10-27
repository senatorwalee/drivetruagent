"""Validation of the menu data file. No database needed."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from drivetruagent.enums import Allergen, Diet, DietStatus, Language
from drivetruagent.ordering.photos import check_photo
from drivetruagent.seed import MenuSeed, OptionGroupSeed, load_menu, photos_dir_for

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
    assert len(items) == 21


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


# --- Specials ---------------------------------------------------------------


def special(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": "fries_days",
        "item": "fries",
        "name": name("Fries special", "Spécial frites"),
        "discount_cents": 50,
        "weekdays": [1, 2],
    }
    return values | overrides


def menu_with_specials(*specials: dict[str, Any]) -> dict[str, Any]:
    """Fries at 249."""
    return menu(item()) | {"specials": list(specials)}


def test_the_real_menu_has_a_special_for_every_day_of_the_week() -> None:
    """So "what's today's special?" has an answer whenever the demo is run."""
    specials = load_menu(MENU_PATH).specials

    covered = {day for each in specials for day in each.weekdays}
    assert covered == {1, 2, 3, 4, 5, 6, 7}
    # One a day, each on a different item, so a week of specials reads as seven.
    assert sorted(day for each in specials for day in each.weekdays) == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
    ]
    assert len({each.item for each in specials}) == 7


def test_the_real_menus_specials_are_described_in_both_languages() -> None:
    for each in load_menu(MENU_PATH).specials:
        assert each.description is not None, each.code


def test_a_menu_needs_no_specials() -> None:
    assert MenuSeed.model_validate(menu(item())).specials == []


def test_a_valid_special_is_accepted() -> None:
    (loaded,) = MenuSeed.model_validate(menu_with_specials(special())).specials

    assert (loaded.item, loaded.discount_cents, loaded.weekdays) == (
        "fries",
        50,
        [1, 2],
    )


def test_weekdays_left_out_means_every_day() -> None:
    every_day = special()
    del every_day["weekdays"]

    (loaded,) = MenuSeed.model_validate(menu_with_specials(every_day)).specials

    assert loaded.weekdays == []


def test_weekdays_are_stored_in_order() -> None:
    (loaded,) = MenuSeed.model_validate(
        menu_with_specials(special(weekdays=[7, 1, 3]))
    ).specials

    assert loaded.weekdays == [1, 3, 7]


@pytest.mark.parametrize("weekdays", [[0], [8], [1, 1], [-1]])
def test_a_bad_weekday_is_rejected(weekdays: list[int]) -> None:
    with pytest.raises(ValidationError, match="weekdays"):
        MenuSeed.model_validate(menu_with_specials(special(weekdays=weekdays)))


def test_a_special_on_an_unknown_item_is_rejected() -> None:
    with pytest.raises(ValidationError, match="there is no item lobster"):
        MenuSeed.model_validate(menu_with_specials(special(item="lobster")))


@pytest.mark.parametrize("discount_cents", [0, -50])
def test_a_special_must_take_something_off(discount_cents: int) -> None:
    with pytest.raises(ValidationError, match="discount_cents"):
        MenuSeed.model_validate(
            menu_with_specials(special(discount_cents=discount_cents))
        )


def test_a_special_cannot_make_its_item_free() -> None:
    with pytest.raises(ValidationError, match="not less than the price of fries"):
        MenuSeed.model_validate(menu_with_specials(special(discount_cents=249)))

    MenuSeed.model_validate(menu_with_specials(special(discount_cents=248)))


def test_two_specials_with_one_code_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate special code"):
        MenuSeed.model_validate(menu_with_specials(special(), special()))


def test_two_specials_may_name_one_item() -> None:
    """Allowed, because they may run on different days. On a shared day the
    earlier one applies."""
    loaded = MenuSeed.model_validate(
        menu_with_specials(special(), special(code="fries_weekend", weekdays=[6, 7]))
    )

    assert len(loaded.specials) == 2


# --- Allergens --------------------------------------------------------------


def test_every_item_in_the_real_menu_has_reviewed_allergens() -> None:
    loaded = load_menu(MENU_PATH)

    unreviewed = [
        item.code
        for category in loaded.categories
        for item in category.items
        if item.allergens is None
    ]
    assert unreviewed == []


def test_the_real_menus_cheese_option_adds_milk() -> None:
    loaded = load_menu(MENU_PATH)
    burger = next(
        item
        for category in loaded.categories
        for item in category.items
        if item.code == "classic_burger"
    )
    cheese = next(o for o in burger.option_groups[0].options if o.code == "cheese")

    assert cheese.allergens is not None
    assert cheese.allergens.contains == [Allergen.MILK]


def reviewed(**allergens: Any) -> dict[str, Any]:
    return {"reviewed_on": "2026-10-01", **allergens}


def test_allergens_with_a_reason_for_may_contain_are_accepted() -> None:
    loaded = MenuSeed.model_validate(
        menu(
            item(
                allergens=reviewed(
                    contains=["wheat"],
                    may_contain=[
                        {
                            "allergens": ["milk", "egg"],
                            "because": name("Fryer", "Friteuse"),
                        }
                    ],
                )
            )
        )
    )

    allergens = loaded.categories[0].items[0].allergens
    assert allergens is not None
    assert [(a.value, level.value) for a, level, _ in allergens.declarations()] == [
        ("wheat", "contains"),
        ("milk", "may_contain"),
        ("egg", "may_contain"),
    ]


def test_an_item_can_be_reviewed_with_nothing_declared() -> None:
    loaded = MenuSeed.model_validate(menu(item(allergens=reviewed())))

    allergens = loaded.categories[0].items[0].allergens
    assert allergens is not None
    assert allergens.declarations() == []


def test_an_unknown_allergen_is_rejected() -> None:
    with pytest.raises(ValidationError, match="contains"):
        MenuSeed.model_validate(menu(item(allergens=reviewed(contains=["celery"]))))


def test_an_allergen_listed_twice_is_rejected() -> None:
    with pytest.raises(ValidationError, match="allergen"):
        MenuSeed.model_validate(
            menu(
                item(
                    allergens=reviewed(
                        contains=["milk"],
                        may_contain=[{"allergens": ["milk"], "because": name()}],
                    )
                )
            )
        )


def test_may_contain_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="because"):
        MenuSeed.model_validate(
            menu(item(allergens=reviewed(may_contain=[{"allergens": ["milk"]}])))
        )


def test_reviewed_allergens_need_a_review_date() -> None:
    with pytest.raises(ValidationError, match="reviewed_on"):
        MenuSeed.model_validate(menu(item(allergens={"contains": ["milk"]})))


def test_option_allergens_on_an_unreviewed_item_are_rejected() -> None:
    cheesy = group(
        options=[option("small"), option("cheesy", allergens={"contains": ["milk"]})]
    )

    with pytest.raises(ValidationError, match="no reviewed allergens"):
        MenuSeed.model_validate(menu(item(option_groups=[cheesy])))


# --- Halal and kosher -------------------------------------------------------


def real_item(code: str) -> Any:
    return next(
        item
        for category in load_menu(MENU_PATH).categories
        for item in category.items
        if item.code == code
    )


def test_the_real_menus_fish_burger_is_halal_suitable_but_not_certified() -> None:
    fish = real_item("fish_burger")

    assert set(fish.diets) == {Diet.HALAL}
    assert fish.diets[Diet.HALAL].status is DietStatus.SUITABLE
    assert "fryer" in fish.diets[Diet.HALAL].note.en


def test_nothing_on_the_real_menu_is_kosher_or_certified() -> None:
    """The kitchen holds no certificate of either kind."""
    loaded = load_menu(MENU_PATH)
    entries = [
        (diet, entry.status)
        for category in loaded.categories
        for item in category.items
        for diet, entry in item.diets.items()
    ]

    assert Diet.KOSHER not in {diet for diet, _ in entries}
    assert DietStatus.CERTIFIED not in {status for _, status in entries}


def test_the_real_menus_meat_is_not_listed_for_halal() -> None:
    for code in ("classic_burger", "chicken_burger", "chicken_nuggets"):
        assert real_item(code).diets == {}, code


def test_an_item_needs_no_diets() -> None:
    loaded = MenuSeed.model_validate(menu(item()))

    assert loaded.categories[0].items[0].diets == {}


def test_a_diet_entry_is_accepted() -> None:
    loaded = MenuSeed.model_validate(
        menu(item(diets={"kosher": {"status": "certified", "note": name()}}))
    )

    entry = loaded.categories[0].items[0].diets[Diet.KOSHER]
    assert entry.status is DietStatus.CERTIFIED


def test_an_unknown_diet_is_rejected() -> None:
    with pytest.raises(ValidationError, match="diets"):
        MenuSeed.model_validate(
            menu(item(diets={"vegan": {"status": "suitable", "note": name()}}))
        )


def test_an_unknown_diet_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="status"):
        MenuSeed.model_validate(
            menu(item(diets={"halal": {"status": "probably", "note": name()}}))
        )


def test_a_diet_entry_needs_a_note_in_each_language() -> None:
    with pytest.raises(ValidationError, match="note"):
        MenuSeed.model_validate(
            menu(item(diets={"halal": {"status": "suitable", "note": {"en": "x"}}}))
        )


# --- Photos -----------------------------------------------------------------


def test_every_item_in_the_real_menu_has_a_photo_that_is_accepted() -> None:
    loaded = load_menu(MENU_PATH)
    photos_dir = photos_dir_for(MENU_PATH)

    for category in loaded.categories:
        for menu_item in category.items:
            assert menu_item.photo is not None, menu_item.code
            assert menu_item.photo.credit, menu_item.code
            data = (photos_dir / menu_item.photo.file).read_bytes()
            assert check_photo(data).content_type == "image/jpeg"


def test_photo_file_names_are_plain() -> None:
    with pytest.raises(ValidationError, match="photo"):
        MenuSeed.model_validate(menu(item(photo={"file": "../secrets.jpg"})))
