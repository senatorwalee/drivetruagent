"""The allergen matrix: seeded from the menu file, read by the agent's tool
and the API, and handed to a person when an item was never reviewed."""

from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.db.models import AllergenDeclaration, Handoff, MenuItem
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.sessions import get_session, open_session, set_language
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
    return ToolContext(db_session, session_id, Decimal("0.13"), LIMITS)


async def check(
    context: ToolContext,
    item_code: str | None,
    allergens: list[str] | None = None,
    selections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return await run_tool(
        context,
        "check_allergens",
        {"item_code": item_code, "selections": selections, "allergens": allergens},
    )


async def add(
    context: ToolContext, item_code: str, selections: list[dict[str, Any]]
) -> None:
    result = await run_tool(
        context,
        "add_item",
        {"item_code": item_code, "quantity": 1, "selections": selections},
    )
    assert result["decision"] == "allow", result


async def never_reviewed(db: AsyncSession, item_code: str) -> None:
    item_id = (
        await db.execute(select(MenuItem.id).where(MenuItem.code == item_code))
    ).scalar_one()
    await db.execute(
        update(MenuItem)
        .where(MenuItem.id == item_id)
        .values(allergens_reviewed_on=None)
    )
    await db.execute(
        delete(AllergenDeclaration).where(AllergenDeclaration.item_id == item_id)
    )
    await db.commit()


def allergens_of(views: list[dict[str, Any]]) -> list[str]:
    return [view["allergen"] for view in views]


# --- Seeding ----------------------------------------------------------------


async def declaration_count(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(AllergenDeclaration))
    ).scalar_one()


async def test_the_menu_files_allergens_are_loaded(db_session: AsyncSession) -> None:
    summary = await seed_menu(db_session, load_menu(MENU_PATH))

    assert summary.allergen_declarations > 0
    assert await declaration_count(db_session) == summary.allergen_declarations
    unreviewed = (
        await db_session.execute(
            select(func.count()).where(MenuItem.allergens_reviewed_on.is_(None))
        )
    ).scalar_one()
    assert unreviewed == 0


async def test_seeding_twice_keeps_one_set_of_declarations(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    first = await seed_menu(db_session, menu)

    await seed_menu(db_session, menu)

    assert await declaration_count(db_session) == first.allergen_declarations


async def test_an_item_taken_out_of_review_loses_its_declarations(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)
    burgers = menu.categories[0]
    burger = burgers.items[0].model_copy(
        update={"allergens": None, "option_groups": []}
    )
    changed = menu.model_copy(
        update={
            "categories": [
                burgers.model_copy(update={"items": [burger, *burgers.items[1:]]}),
                *menu.categories[1:],
            ]
        }
    )

    await seed_menu(db_session, changed)

    item = (
        await db_session.execute(select(MenuItem).where(MenuItem.code == burger.code))
    ).scalar_one()
    assert item.allergens_reviewed_on is None
    left = await db_session.execute(
        select(func.count()).where(AllergenDeclaration.item_id == item.id)
    )
    assert left.scalar_one() == 0


# --- One item ---------------------------------------------------------------


async def test_an_item_lists_what_it_contains_and_may_contain(
    context: ToolContext,
) -> None:
    result = await check(context, "classic_burger")

    (burger,) = result["items"]
    assert (burger["item_name"], burger["verified"]) == ("Classic burger", True)
    assert allergens_of(burger["contains"]) == [
        "sesame",
        "egg",
        "soy",
        "wheat",
        "gluten",
        "mustard",
    ]
    assert burger["may_contain"] == [
        {"allergen": "milk", "because": ["Cooked on the same grill as cheese."]}
    ]
    assert burger["options_that_add"] == [
        {
            "group_code": "extras",
            "option_code": "cheese",
            "option_name": "Cheese",
            "contains": ["milk"],
            "may_contain": [],
        }
    ]
    assert "Never say something is safe" in result["message"]


async def test_the_customers_choices_are_taken_into_account(
    context: ToolContext,
) -> None:
    result = await check(
        context,
        "classic_burger",
        ["milk"],
        [{"group": "extras", "options": ["cheese"]}],
    )

    (burger,) = result["items"]
    assert burger["options"] == ["Cheese"]
    assert burger["contains"] == [{"allergen": "milk", "sources": ["Cheese"]}]
    assert burger["may_contain"] == []


async def test_a_choice_not_given_counts_as_its_default(context: ToolContext) -> None:
    """Fries are plain unless asked for seasoned, and seasoning adds milk. In
    their own fryer, plain fries pick up nothing from other items' batter."""
    result = await check(context, "fries", ["milk", "peanut"])

    (fries,) = result["items"]
    assert "Plain" in fries["options"]
    assert (fries["contains"], fries["may_contain"]) == ([], [])
    assert sorted(fries["not_listed"]) == ["milk", "peanut"]
    assert [o["option_code"] for o in fries["options_that_add"]] == ["seasoned"]


async def test_answers_are_in_the_customers_language(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await set_language(db_session, context.session_id, Language.FR)

    (burger,) = (await check(context, "classic_burger", ["milk"]))["items"]

    assert burger["item_name"] == "Hamburger classique"
    assert burger["may_contain"][0]["because"] == [
        "Cuit sur le même gril que le fromage."
    ]


async def test_an_unknown_item_is_an_error_the_agent_can_read(
    context: ToolContext,
) -> None:
    result = await check(context, "lobster_roll")

    assert result["error"]["code"] == "unknown_item"


async def test_an_unknown_choice_is_an_error_the_agent_can_read(
    context: ToolContext,
) -> None:
    result = await check(
        context, "classic_burger", None, [{"group": "extras", "options": ["anchovy"]}]
    )

    assert result["error"]["code"] == "unknown_option"


# --- The whole order --------------------------------------------------------


async def test_the_whole_order_is_checked_line_by_line_with_its_choices(
    context: ToolContext,
) -> None:
    await add(context, "cheeseburger", [])
    await add(
        context,
        "fries",
        [
            {"group": "size", "options": ["large"]},
            {"group": "seasoning", "options": ["seasoned"]},
        ],
    )
    await add(context, "cola", [{"group": "size", "options": ["small"]}])

    result = await check(context, None, ["milk"])

    rows = [
        (item["line_no"], item["item_code"], allergens_of(item["contains"]))
        for item in result["items"]
    ]
    assert rows == [
        (1, "cheeseburger", ["milk"]),
        (2, "fries", ["milk"]),
        (3, "cola", []),
    ]
    assert result["items"][2]["not_listed"] == ["milk"]


async def test_checking_an_empty_order_asks_which_item(context: ToolContext) -> None:
    result = await check(context, None)

    assert result["items"] == []
    assert "Ask which item" in result["message"]


# --- Never reviewed: a person answers ---------------------------------------


async def handoff_for(db: AsyncSession, context: ToolContext) -> Handoff:
    return (
        await db.execute(
            select(Handoff).where(Handoff.session_id == context.session_id)
        )
    ).scalar_one()


async def test_an_item_never_reviewed_is_handed_to_a_person(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await never_reviewed(db_session, "apple_pie")

    result = await check(context, "apple_pie", ["tree_nut"])

    assert (result["decision"], result["rule"]) == ("escalate", "allergen_unverified")
    assert "items" not in result
    session = await get_session(db_session, context.session_id)
    assert session.state is SessionState.HANDED_OFF
    handoff = await handoff_for(db_session, context)
    assert (handoff.reason, handoff.details) == (
        "allergen_unverified",
        {
            "item_codes": ["apple_pie"],
            "items": ["Apple pie"],
            "allergens": ["tree_nut"],
        },
    )


async def test_one_unreviewed_line_in_the_order_hands_it_off_naming_that_line(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await never_reviewed(db_session, "apple_pie")
    await add(context, "cookie", [])
    await add(context, "apple_pie", [])

    result = await check(context, None)

    assert result["rule"] == "allergen_unverified"
    handoff = await handoff_for(db_session, context)
    assert handoff.details["items"] == ["Apple pie"]
    assert handoff.details["allergens"] == []


async def test_after_the_session_has_ended_nothing_more_happens(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await never_reviewed(db_session, "apple_pie")
    await check(context, "apple_pie")

    again = await check(context, "apple_pie")

    assert (again["decision"], again["rule"]) == ("reject", "session_ended")


# --- The API ----------------------------------------------------------------


async def test_the_api_serves_an_items_allergens(
    client: httpx.AsyncClient, menu: None
) -> None:
    response = await client.get(
        "/menu/items/veggie_burger/allergens", params={"language": "fr"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["item_name"], body["verified"], body["reviewed_on"]) == (
        "Burger végétarien",
        True,
        "2026-10-01",
    )
    assert allergens_of(body["may_contain"]) == ["peanut", "tree_nut", "milk"]


async def test_the_api_says_when_an_item_was_never_reviewed(
    client: httpx.AsyncClient, db_session: AsyncSession, menu: None
) -> None:
    await never_reviewed(db_session, "apple_pie")

    body = (await client.get("/menu/items/apple_pie/allergens")).json()

    assert (body["verified"], body["contains"], body["may_contain"]) == (False, [], [])


async def test_the_api_says_an_unknown_item_is_not_found(
    client: httpx.AsyncClient, menu: None
) -> None:
    response = await client.get("/menu/items/lobster_roll/allergens")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"
