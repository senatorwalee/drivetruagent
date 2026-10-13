"""The instructions the model is given. No database."""

import re

from drivetruagent.agent.prompt import render_instructions
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.ordering.views import CategoryView, MenuItemSummary, MenuView


def menu(fries_name: str, cola_name: str, sides: str, drinks: str) -> MenuView:
    return MenuView(
        categories=[
            CategoryView(
                category_code="sides",
                category_name=sides,
                items=[
                    MenuItemSummary(
                        item_code="fries",
                        item_name=fries_name,
                        base_price_cents=249,
                        must_ask=["size"],
                    )
                ],
            ),
            CategoryView(
                category_code="drinks",
                category_name=drinks,
                items=[
                    MenuItemSummary(
                        item_code="cola",
                        item_name=cola_name,
                        base_price_cents=199,
                        must_ask=["size"],
                    )
                ],
            ),
        ]
    )


INSTRUCTIONS = render_instructions(
    "Maple Lane Burgers",
    menu("Fries", "Cola", "Sides", "Drinks"),
    menu("Frites", "Cola", "Accompagnements", "Boissons"),
)


def test_the_restaurant_is_named() -> None:
    assert "Maple Lane Burgers" in INSTRUCTIONS


def test_the_menu_index_lists_codes_with_both_names() -> None:
    assert "- fries: Fries / Frites" in INSTRUCTIONS
    assert "- cola: Cola / Cola" in INSTRUCTIONS
    assert INSTRUCTIONS.index("Sides:") < INSTRUCTIONS.index("Drinks:")


def test_the_menu_index_holds_no_prices() -> None:
    """Prices come from tools, so a menu price change needs no prompt change
    and the model has nothing to recite from memory."""
    index = INSTRUCTIONS[INSTRUCTIONS.index("# Menu index") :]

    assert "249" not in index
    assert "199" not in index
    assert not re.search(r"\d", index)


def test_every_tool_the_instructions_mention_exists() -> None:
    tool_names = {tool.name for tool in TOOL_DEFINITIONS}
    mentioned = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", INSTRUCTIONS))

    # Item codes with underscores would also match; this menu has none.
    assert mentioned <= tool_names


def test_the_key_behaviours_are_stated() -> None:
    for tool in (
        "set_language",
        "search_menu",
        "request_confirmation",
        "submit_order",
        "confirm_pending_action",
        "cancel_pending_action",
        "request_human",
    ):
        assert tool in INSTRUCTIONS


def test_the_instructions_are_the_same_every_time() -> None:
    """Identical instructions let the provider cache them between requests."""
    again = render_instructions(
        "Maple Lane Burgers",
        menu("Fries", "Cola", "Sides", "Drinks"),
        menu("Frites", "Cola", "Accompagnements", "Boissons"),
    )

    assert again == INSTRUCTIONS


def test_the_model_is_told_it_cannot_grant_a_saving() -> None:
    """Wording only: the backend has no way to apply a discount on request."""
    combos = INSTRUCTIONS[INSTRUCTIONS.index("# Combos and savings") :]

    assert "You never add, apply or promise one yourself." in combos
    assert "offer it once" in combos
    assert "never offer one again" in combos
