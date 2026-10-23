"""What the menu page may send, and the codes made for new items."""

from typing import Any

import pytest
from pydantic import ValidationError

from drivetruagent.ordering.menu_admin import ItemChanges, NewItem, code_from_name


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Spicy wrap", "spicy_wrap"),
        ("  Crème brûlée! ", "creme_brulee"),
        ("Fish & chips (large)", "fish_chips_large"),
        ("7-Up", "7_up"),
        ("!!!", "item"),
        ("a" * 60, "a" * 40),
    ],
)
def test_codes_are_made_from_the_english_name(name: str, code: str) -> None:
    assert code_from_name(name) == code


def new_item(**overrides: Any) -> dict[str, Any]:
    return {
        "name_en": "Spicy wrap",
        "category_code": "burgers",
        "base_price_cents": 699,
    } | overrides


def test_new_item_trims_text_and_treats_blank_as_none() -> None:
    item = NewItem.model_validate(
        new_item(name_en="  Spicy wrap ", name_fr="", description_en="  ")
    )

    assert item.name_en == "Spicy wrap"
    assert item.name_fr is None
    assert item.description_en is None
    assert item.stock is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name_en": "   "},
        {"base_price_cents": -1},
        {"base_price_cents": 100_001},
        {"stock": -1},
        {"name_en": "x" * 61},
        {"description_fr": "Épicé", "description_en": None},
        {"code": "chosen_by_caller"},
    ],
)
def test_new_item_refuses_bad_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NewItem.model_validate(new_item(**overrides))


def test_changes_remember_which_fields_were_sent() -> None:
    changes = ItemChanges.model_validate({"stock": None, "base_price_cents": 549})

    assert changes.model_fields_set == {"stock", "base_price_cents"}


@pytest.mark.parametrize(
    "body",
    [
        {"name_en": None},
        {"category_code": None},
        {"base_price_cents": None},
        {"is_active": None},
        {"description_en": "", "description_fr": "Épicé"},
    ],
)
def test_changes_cannot_clear_what_an_item_must_have(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ItemChanges.model_validate(body)


def test_a_french_description_alone_is_fine_when_english_is_kept() -> None:
    changes = ItemChanges.model_validate({"description_fr": "Épicé"})

    assert changes.description_fr == "Épicé"
