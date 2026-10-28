"""Requests the menu can't fill exactly: what the search reports, what the
agent is told to offer, what is never offered to someone avoiding an
allergen, and the count kept for insights. Real menu, real allergen matrix."""

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.messages import ToolResult
from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.enums import Language
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.menu import search_menu
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits

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


async def ask(context: ToolContext, query: str) -> dict[str, Any]:
    return await run_tool(context, "search_menu", {"query": query})


# --- What the search reports ------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "a large fries",
        "a medium fries and a cheeseburger",
        "cheeseburger with bacon",
        "two vanilla shakes",
        "coke",
    ],
)
async def test_sizes_choices_counts_and_other_items_are_exact(
    db_session: AsyncSession, menu: None, query: str
) -> None:
    (top, *_) = await search_menu(db_session, query, Language.EN)

    assert (top.match, top.not_offered) == ("exact", [])


async def test_a_french_size_is_exact_in_either_gender(
    db_session: AsyncSession, menu: None
) -> None:
    """The menu says Grand; a guest says "une grande frite"."""
    matches = await search_menu(
        db_session, "une grande frite et un lait frappé", Language.FR
    )

    assert {(m.item_code, m.match) for m in matches[:2]} == {
        ("fries", "exact"),
        ("milkshake", "exact"),
    }


@pytest.mark.parametrize(
    ("query", "item", "missing", "elsewhere"),
    [
        ("oat milk shake", "milkshake", ["oat"], ["coffee"]),
        ("spicy nuggets", "chicken_nuggets", ["spicy"], ["chicken_burger"]),
        ("iced coffee", "coffee", ["iced"], ["iced_tea"]),
        ("diet coke", "cola", ["diet"], []),
    ],
)
async def test_a_close_match_names_what_is_missing_and_where_it_is_offered(
    db_session: AsyncSession,
    menu: None,
    query: str,
    item: str,
    missing: list[str],
    elsewhere: list[str],
) -> None:
    (top, *_) = await search_menu(db_session, query, Language.EN)

    assert (top.item_code, top.match, top.not_offered) == (item, "close", missing)
    assert [other.item_code for other in top.offered_elsewhere] == elsewhere


# --- What the agent is told -------------------------------------------------


async def test_nothing_matching_is_no_match_and_never_invented(
    context: ToolContext,
) -> None:
    result = await ask(context, "lobster roll")

    assert (result["match"], result["matches"]) == ("none", [])
    assert "Never invent an item" in result["message"]


async def test_a_similar_item_is_offered_when_nothing_is_being_avoided(
    context: ToolContext,
) -> None:
    result = await ask(context, "spicy nuggets")

    assert result["match"] == "close"
    assert "avoiding" not in result
    assert "Chicken nuggets has no spicy" in result["message"]
    assert "Crispy chicken burger does have spicy" in result["message"]


async def test_what_the_customer_is_avoiding_is_never_suggested(
    context: ToolContext,
) -> None:
    """An oat milk shake: the regular shake contains milk, so it isn't offered."""
    result = await ask(context, "an oat milk shake")

    assert result["avoiding"] == ["milk"]
    milkshake = result["matches"][0]
    assert (milkshake["item_code"], milkshake["contains_avoided"]) == (
        "milkshake",
        ["milk"],
    )
    assert "You may offer Milkshake" not in result["message"]
    offered = [option["item_code"] for option in result["options_without"]]
    assert offered and "milkshake" not in offered
    assert offered == ["cola", "lemon_lime_soda", "iced_tea"]
    assert "never call anything safe" in result["message"]


async def test_may_contain_counts_as_contains_for_someone_avoiding_it(
    context: ToolContext,
) -> None:
    """The cookie and the sundae may contain nuts: neither is offered."""
    result = await ask(context, "a nut-free dessert")

    assert result["avoiding"] == ["tree_nut"]
    offered = [option["item_code"] for option in result["options_without"]]
    assert offered
    assert "cookie" not in offered
    assert "sundae" not in offered


async def test_fries_in_their_own_fryer_are_offered_without_gluten(
    context: ToolContext,
) -> None:
    """They share no oil with the breaded items, so they pick up no gluten."""
    result = await ask(context, "gluten-free bun")

    assert (result["match"], result["avoiding"]) == ("none", ["gluten"])
    offered = [option["item_code"] for option in result["options_without"]]
    assert offered[0] == "fries"


async def test_a_sold_out_item_is_not_offered_instead(
    db_session: AsyncSession, context: ToolContext
) -> None:
    await set_on_hand(db_session, "cola", 0)
    await db_session.commit()

    result = await ask(context, "an oat milk shake")

    assert "cola" not in [option["item_code"] for option in result["options_without"]]


# --- Counted for insights ---------------------------------------------------


async def test_requests_the_menu_couldnt_fill_are_counted(
    client: httpx.AsyncClient, db_session: AsyncSession, context: ToolContext
) -> None:
    turn_id = uuid.uuid4()
    for n, query in enumerate(
        ["Oat milk shake", "oat milk shake ", "gluten-free bun", "a large fries"]
    ):
        result = await ask(context, query)
        # Recorded as the agent loop records every tool result.
        await events.record_message(
            db_session,
            context.session_id,
            turn_id,
            ToolResult(f"c{n}", "search_menu", result),
        )
    await db_session.commit()

    response = await client.get("/insights/unmet-requests", params={"days": 7})

    assert response.status_code == 200
    rows = [(row["request"], row["times"], row["match"]) for row in response.json()]
    assert rows == [("oat milk shake", 2, "close"), ("gluten-free bun", 1, "none")]
