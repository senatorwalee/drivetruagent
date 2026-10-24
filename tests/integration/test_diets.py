"""Halal and kosher answers come from the reviewed list in the menu file, and a
swap to what the guest can eat happens in one step. Real menu, real database."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.db.models import DietSuitability
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)


@pytest.fixture
async def context(db_session: AsyncSession, menu: None) -> ToolContext:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return ToolContext(db_session, session_id, Decimal("0.13"), LIMITS)


def size(option: str) -> list[dict[str, Any]]:
    return [{"group": "size", "options": [option]}]


async def add(
    context: ToolContext, item_code: str, quantity: int = 1, **arguments: Any
) -> dict[str, Any]:
    return await run_tool(
        context,
        "add_item",
        {"item_code": item_code, "quantity": quantity, "selections": [], **arguments},
    )


async def check(
    context: ToolContext, diet: str, item_code: str | None = None
) -> dict[str, Any]:
    return await run_tool(context, "check_diet", {"diet": diet, "item_code": item_code})


async def replace(
    context: ToolContext, line_no: int, item_code: str, **arguments: Any
) -> dict[str, Any]:
    return await run_tool(
        context,
        "replace_item",
        {
            "line_no": line_no,
            "item_code": item_code,
            "quantity": None,
            "selections": [],
            **arguments,
        },
    )


def codes(entries: list[dict[str, Any]]) -> list[str]:
    return [entry["item_code"] for entry in entries]


# --- Seeding ----------------------------------------------------------------


async def test_the_menu_files_diets_are_loaded(db_session: AsyncSession) -> None:
    summary = await seed_menu(db_session, load_menu(MENU_PATH))

    rows = (await db_session.execute(select(func.count(DietSuitability.id)))).scalar()
    assert summary.diet_entries == rows == 1


async def test_seeding_twice_keeps_one_row_per_item_and_diet(
    db_session: AsyncSession,
) -> None:
    await seed_menu(db_session, load_menu(MENU_PATH))
    await seed_menu(db_session, load_menu(MENU_PATH))

    rows = (await db_session.execute(select(func.count(DietSuitability.id)))).scalar()
    assert rows == 1


# --- Asking about one item --------------------------------------------------


async def test_the_chicken_is_not_listed_and_the_fish_burger_is_offered(
    context: ToolContext,
) -> None:
    result = await check(context, "halal", "chicken_burger")

    assert result["items"][0]["status"] is None
    assert codes(result["options"]) == ["fish_burger"]
    assert result["options"][0]["status"] == "suitable"
    assert "fryer" in result["options"][0]["note"]
    assert "not certified" in result["message"]


async def test_the_fish_burger_is_suitable_but_never_called_certified(
    context: ToolContext,
) -> None:
    result = await check(context, "halal", "fish_burger")

    assert result["items"][0]["status"] == "suitable"
    assert "only if its status is certified" in result["message"]


async def test_a_side_is_answered_with_an_option_from_the_whole_menu(
    context: ToolContext,
) -> None:
    """Nothing among the sides is listed, so the offer comes from anywhere."""
    result = await check(context, "halal", "chicken_nuggets")

    assert codes(result["options"]) == ["fish_burger"]


async def test_nothing_is_offered_as_kosher(context: ToolContext) -> None:
    result = await check(context, "kosher", "fish_burger")

    assert result["items"][0]["status"] is None
    assert result["options"] == []
    assert "Nothing on the menu is listed for kosher" in result["message"]


async def test_the_answer_follows_the_session_language(context: ToolContext) -> None:
    await run_tool(context, "set_language", {"language": "fr"})

    result = await check(context, "halal", "chicken_burger")

    assert result["options"][0]["item_name"] == "Burger au poisson"
    assert "friteuse" in result["options"][0]["note"]


async def test_an_unknown_item_is_an_error_result(context: ToolContext) -> None:
    result = await check(context, "halal", "lobster")

    assert result["error"]["code"] == "unknown_item"


async def test_a_sold_out_fish_burger_is_not_offered(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await set_on_hand(db_session, "fish_burger", 0)
    await db_session.commit()

    result = await check(context, "halal", "chicken_burger")

    assert result["options"] == []
    assert "Nothing on the menu is listed for halal" in result["message"]


# --- Asking about the order -------------------------------------------------


async def test_every_line_that_is_not_listed_is_named(context: ToolContext) -> None:
    await add(context, "cheeseburger")
    await add(context, "fish_burger")
    await add(context, "cola", selections=size("large"))

    result = await check(context, "halal")

    assert [item["line_no"] for item in result["items"]] == [1, 2, 3]
    assert result["not_suitable_lines"] == [1, 3]
    assert codes(result["options"]) == ["fish_burger"]


async def test_an_empty_order_still_gets_the_options(context: ToolContext) -> None:
    result = await check(context, "halal")

    assert (result["items"], result["not_suitable_lines"]) == ([], [])
    assert codes(result["options"]) == ["fish_burger"]


# --- Swapping ---------------------------------------------------------------


async def test_a_swap_keeps_the_line_number_and_quantity(context: ToolContext) -> None:
    await add(
        context,
        "cheeseburger",
        2,
        selections=[{"group": "extras", "options": ["bacon"]}],
    )
    await add(context, "cookie")

    result = await replace(context, 1, "fish_burger")

    assert result["decision"] == "allow"
    lines = result["cart"]["lines"]
    assert [
        (line["line_no"], line["item_code"], line["quantity"]) for line in lines
    ] == [
        (1, "fish_burger", 2),
        (2, "cookie", 1),
    ]
    # The bacon belonged to the cheeseburger and goes with it.
    assert lines[0]["options"] == []


async def test_a_swap_can_carry_the_new_items_choices(context: ToolContext) -> None:
    await add(context, "cheeseburger")

    result = await replace(
        context,
        1,
        "fish_burger",
        selections=[{"group": "extras", "options": ["cheese"]}],
    )

    (line,) = result["cart"]["lines"]
    assert [option["option_code"] for option in line["options"]] == ["cheese"]
    assert line["unit_price_cents"] == 649 + 75


async def test_a_sold_out_swap_leaves_the_original_line(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await add(context, "cheeseburger")
    await set_on_hand(db_session, "fish_burger", 0)
    await db_session.commit()

    result = await replace(context, 1, "fish_burger")

    assert (result["decision"], result["rule"]) == ("reject", "insufficient_stock")
    assert codes(result["cart"]["lines"]) == ["cheeseburger"]


async def test_a_swap_with_a_missing_choice_leaves_the_original_line(
    context: ToolContext,
) -> None:
    await add(context, "cookie")

    result = await replace(context, 1, "cola")

    assert result["error"]["code"] == "missing_required_options"
    assert codes(result["cart"]["lines"]) == ["cookie"]


async def test_a_swap_of_a_line_that_does_not_exist_is_refused(
    context: ToolContext,
) -> None:
    result = await replace(context, 4, "fish_burger")

    assert result["error"]["code"] == "unknown_cart_line"


async def test_a_swap_frees_the_old_items_stock(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await set_on_hand(db_session, "cheeseburger", 1)
    await db_session.commit()
    await add(context, "cheeseburger")

    await replace(context, 1, "fish_burger")
    again = await add(context, "cheeseburger")

    assert again["decision"] == "allow"


async def test_a_swap_to_the_same_item_holds_the_new_quantity(
    db_session: AsyncSession, context: ToolContext
) -> None:
    """3 in stock: 2 swapped for 3 holds all three, so a fourth is refused."""
    await set_on_hand(db_session, "cookie", 3)
    await db_session.commit()
    await add(context, "cookie", 2)

    swapped = await replace(context, 1, "cookie", quantity=3)
    fourth = await add(context, "cookie")

    assert swapped["cart"]["lines"][0]["quantity"] == 3
    assert (fourth["decision"], fourth["rule"]) == ("reject", "insufficient_stock")


async def test_the_combo_still_applies_after_a_swap(context: ToolContext) -> None:
    await add(context, "cheeseburger")
    await add(context, "fries", selections=size("medium"))
    await add(context, "cola", selections=size("medium"))

    result = await replace(context, 1, "fish_burger")

    assert [d["code"] for d in result["cart"]["discounts"]] == ["burger_combo"]


# --- Knowledge search points at the list ------------------------------------


async def test_a_halal_question_to_knowledge_search_is_sent_to_check_diet(
    context: ToolContext,
) -> None:
    result = await run_tool(
        context,
        "search_knowledge",
        {"query": "Is your chicken halal?", "item_code": None},
    )

    assert result["check_diet_first"] == ["halal"]


async def test_other_questions_to_knowledge_search_are_left_alone(
    context: ToolContext,
) -> None:
    result = await run_tool(
        context,
        "search_knowledge",
        {"query": "What oil do you use?", "item_code": None},
    )

    assert "check_diet_first" not in result
