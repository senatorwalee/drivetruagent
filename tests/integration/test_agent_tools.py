"""Each tool runs the right thing, against a real database and the real menu."""

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.sessions import get_session, open_session
from drivetruagent.policy.decisions import PolicyLimits

LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)


@pytest.fixture
async def context(db_session: AsyncSession, menu: None) -> ToolContext:
    session_id = (await open_session(db_session)).id
    return ToolContext(db_session, session_id, Decimal("0.13"), LIMITS)


def size(option: str) -> list[dict[str, Any]]:
    return [{"group": "size", "options": [option]}]


async def add(context: ToolContext, item_code: str, **arguments: Any) -> dict[str, Any]:
    return await run_tool(
        context,
        "add_item",
        {"item_code": item_code, "quantity": 1, "selections": [], **arguments},
    )


# --- Menu tools -------------------------------------------------------------


async def test_search_menu_returns_ranked_matches(context: ToolContext) -> None:
    result = await run_tool(context, "search_menu", {"query": "a large fries"})

    best = result["matches"][0]
    assert (best["item_code"], best["must_ask"]) == ("fries", ["size"])


async def test_search_menu_with_no_match_returns_an_empty_list(
    context: ToolContext,
) -> None:
    assert await run_tool(context, "search_menu", {"query": "lobster"}) == {
        "matches": []
    }


async def test_get_item_details_lists_the_choices(context: ToolContext) -> None:
    result = await run_tool(context, "get_item_details", {"item_code": "fries"})

    assert [choice["group_code"] for choice in result["choices"]] == [
        "size",
        "style",
        "seasoning",
    ]
    assert result["choices"][0]["must_ask"] is True


async def test_get_item_details_for_an_unknown_item_is_an_error_result(
    context: ToolContext,
) -> None:
    result = await run_tool(context, "get_item_details", {"item_code": "lobster"})

    assert result["error"]["code"] == "unknown_item"


async def test_menu_tools_follow_the_session_language(context: ToolContext) -> None:
    await run_tool(context, "set_language", {"language": "fr"})

    search = await run_tool(context, "search_menu", {"query": "frites"})
    details = await run_tool(context, "get_item_details", {"item_code": "fries"})

    assert search["matches"][0]["item_name"] == "Frites"
    assert details["choices"][0]["group_name"] == "Format"


# --- Cart tools go through the policy gateway -------------------------------


async def test_add_item_runs_and_returns_the_cart(context: ToolContext) -> None:
    result = await add(context, "fries", quantity=2, selections=size("large"))

    assert result["decision"] == "allow"
    assert result["cart"]["subtotal_cents"] == 758
    # Fields that do not apply are left out, to keep results short.
    assert "error" not in result
    assert "pending_action_id" not in result


async def test_add_item_without_a_required_choice_says_what_to_ask(
    context: ToolContext,
) -> None:
    result = await add(context, "fries")

    assert result["decision"] == "reject"
    assert result["error"]["code"] == "missing_required_options"
    missing = result["error"]["details"]["missing"]
    assert [option["option_code"] for option in missing[0]["options"]] == [
        "small",
        "medium",
        "large",
    ]


async def test_a_group_given_twice_has_its_options_combined(
    context: ToolContext,
) -> None:
    result = await add(
        context,
        "chicken_nuggets",
        selections=[
            {"group": "count", "options": ["6_pieces"]},
            {"group": "dip", "options": ["ketchup"]},
            {"group": "dip", "options": ["barbecue"]},
        ],
    )

    chosen = [o["option_code"] for o in result["cart"]["lines"][0]["options"]]
    assert chosen == ["6_pieces", "ketchup", "barbecue"]


async def test_update_item_changes_only_what_is_given(context: ToolContext) -> None:
    await add(
        context,
        "fries",
        selections=[*size("small"), {"group": "style", "options": ["curly"]}],
    )

    quantity_only = await run_tool(
        context, "update_item", {"line_no": 1, "quantity": 3, "selections": None}
    )
    size_only = await run_tool(
        context,
        "update_item",
        {"line_no": 1, "quantity": None, "selections": size("large")},
    )

    line = size_only["cart"]["lines"][0]
    chosen = {o["group_code"]: o["option_code"] for o in line["options"]}
    assert quantity_only["cart"]["lines"][0]["quantity"] == 3
    assert line["quantity"] == 3
    assert chosen == {"size": "large", "style": "curly", "seasoning": "plain"}


async def test_remove_item_and_get_cart(context: ToolContext) -> None:
    await add(context, "cookie")
    await add(context, "apple_pie")

    removed = await run_tool(context, "remove_item", {"line_no": 1})
    cart = await run_tool(context, "get_cart", {})

    assert removed["decision"] == "allow"
    assert [line["item_code"] for line in cart["cart"]["lines"]] == ["apple_pie"]
    assert cart["cart"] == removed["cart"]


async def test_submit_is_refused_until_the_order_is_read_back(
    context: ToolContext,
) -> None:
    await add(context, "cookie")

    too_soon = await run_tool(context, "submit_order", {})
    read_back = await run_tool(context, "request_confirmation", {})
    placed = await run_tool(context, "submit_order", {})

    assert too_soon["error"]["code"] == "order_not_confirmed"
    assert read_back["cart"]["state"] == "confirming"
    assert placed["decision"] == "allow"
    assert placed["order"]["order_number"] >= 1


async def test_unusual_quantity_is_held_then_confirmed(context: ToolContext) -> None:
    held = await add(context, "cookie", quantity=15)

    confirmed = await run_tool(
        context,
        "confirm_pending_action",
        {"pending_action_id": held["pending_action_id"]},
    )

    assert held["decision"] == "confirm"
    assert held["cart"]["lines"] == []
    assert confirmed["decision"] == "allow"
    assert confirmed["cart"]["item_count"] == 15


async def test_a_held_action_can_be_cancelled(context: ToolContext) -> None:
    held = await add(context, "cookie", quantity=15)

    cancelled = await run_tool(
        context,
        "cancel_pending_action",
        {"pending_action_id": held["pending_action_id"]},
    )

    assert cancelled["rule"] == "pending_action_cancelled"
    assert cancelled["cart"]["lines"] == []


async def test_an_unknown_pending_action_is_an_error_result(
    context: ToolContext,
) -> None:
    result = await run_tool(
        context, "confirm_pending_action", {"pending_action_id": str(uuid.uuid4())}
    )

    assert result["error"]["code"] == "pending_action_not_found"


# --- Session tools ----------------------------------------------------------


async def test_set_language_updates_the_session(context: ToolContext) -> None:
    result = await run_tool(context, "set_language", {"language": "fr"})

    customer_session = await get_session(context.db, context.session_id)
    assert result["language"] == "fr"
    assert customer_session.language is Language.FR


async def test_request_human_hands_the_session_off(context: ToolContext) -> None:
    await add(context, "cookie")

    result = await run_tool(
        context, "request_human", {"reason": "customer asked for a person"}
    )

    customer_session = await get_session(context.db, context.session_id)
    assert (result["decision"], result["rule"]) == ("escalate", "human_requested")
    assert customer_session.state is SessionState.HANDED_OFF
    assert customer_session.end_reason == (
        "human_requested: customer asked for a person"
    )
    # The cart is kept for the person taking over.
    assert [line["item_code"] for line in result["cart"]["lines"]] == ["cookie"]


async def test_request_human_on_an_ended_session_is_rejected(
    context: ToolContext,
) -> None:
    await run_tool(context, "request_human", {"reason": "first"})

    again = await run_tool(context, "request_human", {"reason": "second"})

    assert (again["decision"], again["rule"]) == ("reject", "session_ended")


async def test_tools_reject_changes_after_a_handoff(context: ToolContext) -> None:
    await run_tool(context, "request_human", {"reason": "asked"})

    result = await add(context, "cookie")
    language = await run_tool(context, "set_language", {"language": "fr"})

    assert result["error"]["code"] == "session_ended"
    assert language["error"]["code"] == "session_ended"


# --- Bad calls from the model -----------------------------------------------


async def test_an_unknown_tool_is_an_error_result_listing_the_real_ones(
    context: ToolContext,
) -> None:
    result = await run_tool(context, "give_discount", {"percent": 100})

    assert result["error"]["code"] == "invalid_tool_call"
    assert "add_item" in result["error"]["details"]["valid_tools"]


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("add_item", {"item_code": "cookie"}, "quantity"),
        (
            "add_item",
            {"item_code": "cookie", "quantity": "two", "selections": []},
            "quantity",
        ),
        (
            "add_item",
            {"item_code": "cookie", "quantity": 1, "selections": [], "price_cents": 0},
            "price_cents",
        ),
        ("remove_item", {}, "line_no"),
        ("set_language", {"language": "de"}, "language"),
        (
            "confirm_pending_action",
            {"pending_action_id": "not-an-id"},
            "pending_action_id",
        ),
        ("get_cart", {"verbose": True}, "verbose"),
    ],
)
async def test_bad_arguments_are_an_error_result_naming_the_field(
    context: ToolContext, name: str, arguments: dict[str, Any], field: str
) -> None:
    result = await run_tool(context, name, arguments)

    assert result["error"]["code"] == "invalid_tool_call"
    assert field in {p["field"] for p in result["error"]["details"]["problems"]}
    cart = await run_tool(context, "get_cart", {})
    assert cart["cart"]["lines"] == []


async def test_results_can_be_sent_as_json(context: ToolContext) -> None:
    results = [
        await add(context, "fries", selections=size("large")),
        await run_tool(context, "get_cart", {}),
        await run_tool(context, "search_menu", {"query": "burger"}),
        await run_tool(context, "get_item_details", {"item_code": "fries"}),
        await run_tool(context, "request_confirmation", {}),
        await run_tool(context, "submit_order", {}),
    ]

    for result in results:
        assert json.loads(json.dumps(result)) == result
