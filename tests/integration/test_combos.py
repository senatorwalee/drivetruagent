"""Combos against a real database and the real menu.

The arithmetic is covered in tests/unit/test_discounts.py. These tests check
that a cart earns and loses a combo as it changes, that an order keeps what
it was given, and that a combo is suggested once and not again.

From data/menu.json: the burger combo is one burger, one side and one drink,
200 off. Classic burger 549, small fries 249, bottled water 149, cookie 149.
"""

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.messages import ToolCall, ToolResult
from drivetruagent.agent.scripted import ScriptedModel, call, calls, say
from drivetruagent.db.models import (
    ComboRule,
    MenuCategory,
    MenuItem,
    Order,
    OrderDiscount,
    SessionSuggestion,
)
from drivetruagent.enums import DiscountKind, Language, SuggestionStatus
from drivetruagent.ordering.cart import (
    add_item,
    get_cart,
    remove_line,
    request_confirmation,
    update_line,
)
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.offers import close_suggestions, list_combos
from drivetruagent.ordering.orders import get_order, submit_order
from drivetruagent.ordering.sessions import open_session, set_language
from drivetruagent.ordering.views import CartSnapshot, DiscountView
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import Action, AddItem, RemoveLine, UpdateLine
from drivetruagent.policy.decisions import Decision, PolicyLimits
from drivetruagent.policy.outcome import ActionOutcome

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
ONE_COMBO = DiscountView(
    kind=DiscountKind.COMBO,
    code="burger_combo",
    name="Burger combo",
    times=1,
    amount_cents=200,
)

A_BURGER = AddItem(item_code="classic_burger")
SMALL_FRIES = AddItem(item_code="fries", selections={"size": ["small"]})
A_WATER = AddItem(item_code="bottled_water")
A_COOKIE = AddItem(item_code="cookie")

pytestmark = pytest.mark.usefixtures("menu")


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


async def add(
    db: AsyncSession, session_id: uuid.UUID, action: AddItem, quantity: int = 1
) -> CartSnapshot:
    return await add_item(
        db,
        session_id,
        action.item_code,
        quantity,
        action.selections,
        tax_rate=TAX,
    )


async def add_a_full_combo(db: AsyncSession, session_id: uuid.UUID) -> CartSnapshot:
    await add(db, session_id, A_BURGER)
    await add(db, session_id, SMALL_FRIES)
    return await add(db, session_id, A_WATER)


async def propose(
    db: AsyncSession, session_id: uuid.UUID, action: Action, key: str | None = None
) -> ActionOutcome:
    return await gateway.execute(
        db, session_id, action, tax_rate=TAX, limits=LIMITS, idempotency_key=key
    )


def missing(outcome: ActionOutcome) -> list[str] | None:
    """The categories a suggestion says are still needed, or None if there is none."""
    if outcome.suggestion is None:
        return None
    return [category.category_code for category in outcome.suggestion.missing]


# --- Earning and losing a combo ---------------------------------------------


async def test_a_burger_a_side_and_a_drink_make_a_combo(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await add_a_full_combo(db_session, session_id)

    assert cart.discounts == [ONE_COMBO]
    assert cart.subtotal_cents == 947
    assert cart.discount_cents == 200
    # Tax on 747, not on 947.
    assert cart.tax_cents == 97
    assert cart.total_cents == 844


async def test_two_parts_of_three_earn_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add(db_session, session_id, A_BURGER)
    cart = await add(db_session, session_id, SMALL_FRIES)

    assert cart.discounts == []
    assert cart.discount_cents == 0
    assert cart.total_cents == cart.subtotal_cents + cart.tax_cents


async def test_items_outside_the_combo_change_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)
    cart = await add(db_session, session_id, A_COOKIE)

    assert cart.discounts == [ONE_COMBO]
    assert cart.subtotal_cents == 947 + 149


async def test_removing_a_part_removes_the_discount(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)

    cart = await remove_line(db_session, session_id, 3, tax_rate=TAX)

    assert cart.discounts == []
    assert cart.discount_cents == 0
    assert cart.total_cents == 798 + 104


async def test_reading_the_cart_gives_the_same_discount_as_changing_it(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    changed = await add_a_full_combo(db_session, session_id)

    assert await get_cart(db_session, session_id, tax_rate=TAX) == changed


async def test_the_read_back_includes_the_discount(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)

    cart = await request_confirmation(db_session, session_id, tax_rate=TAX)

    assert cart.discounts == [ONE_COMBO]
    assert cart.total_cents == 844


async def test_quantities_count_as_units(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add(db_session, session_id, A_BURGER, 2)
    await add(db_session, session_id, SMALL_FRIES, 2)
    cart = await add(db_session, session_id, A_WATER, 2)

    assert cart.discounts == [
        ONE_COMBO.model_copy(update={"times": 2, "amount_cents": 400})
    ]
    assert cart.discount_cents == 400


async def test_each_unit_counts_toward_one_combo(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Two burgers, one side, one drink: one combo, not two."""
    await add(db_session, session_id, A_BURGER, 2)
    await add(db_session, session_id, SMALL_FRIES)
    cart = await add(db_session, session_id, A_WATER)

    assert cart.discounts == [ONE_COMBO]


async def test_different_items_from_one_category_each_count(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add(db_session, session_id, A_BURGER)
    await add(db_session, session_id, AddItem(item_code="cheeseburger"))
    await add(db_session, session_id, SMALL_FRIES, 2)
    await add(db_session, session_id, A_WATER)
    cart = await add(
        db_session,
        session_id,
        AddItem(item_code="cola", selections={"size": ["small"]}),
    )

    assert cart.discount_cents == 400


async def test_lowering_a_quantity_can_lose_a_combo(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add(db_session, session_id, A_BURGER, 2)
    await add(db_session, session_id, SMALL_FRIES, 2)
    await add(db_session, session_id, A_WATER, 2)

    cart = await update_line(db_session, session_id, 2, quantity=1, tax_rate=TAX)

    assert cart.discount_cents == 200


async def test_the_size_of_the_fries_does_not_matter(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The saving is a fixed amount, so an upgrade still costs what it costs."""
    await add(db_session, session_id, A_BURGER)
    await add(
        db_session,
        session_id,
        AddItem(item_code="fries", selections={"size": ["large"], "style": ["curly"]}),
    )
    cart = await add(db_session, session_id, A_WATER)

    assert cart.subtotal_cents == 549 + 429 + 149
    assert cart.discount_cents == 200


async def test_the_combo_is_named_in_the_customers_language(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)
    await set_language(db_session, session_id, Language.FR)

    cart = await get_cart(db_session, session_id, tax_rate=TAX)

    assert [discount.name for discount in cart.discounts] == ["Trio burger"]


async def test_a_retired_combo_stops_applying(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)

    await db_session.execute(update(ComboRule).values(is_active=False))

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.discounts == []
    assert cart.total_cents == 947 + 123


async def test_a_changed_discount_shows_on_the_next_read(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Nothing about a discount is stored on a cart, so there is nothing to go stale."""
    await add_a_full_combo(db_session, session_id)

    await db_session.execute(update(ComboRule).values(discount_cents=300))

    assert (await get_cart(db_session, session_id, tax_rate=TAX)).discount_cents == 300


# --- Orders -----------------------------------------------------------------


async def test_an_order_is_charged_the_discounted_total(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)

    order = await submit_order(db_session, session_id, tax_rate=TAX)

    assert order.discounts == [ONE_COMBO]
    assert (order.subtotal_cents, order.discount_cents) == (947, 200)
    assert (order.tax_cents, order.total_cents) == (97, 844)
    row = (await db_session.execute(select(Order))).scalar_one()
    assert (row.subtotal_cents, row.discount_cents, row.total_cents) == (947, 200, 844)


async def test_an_order_keeps_its_discount_when_the_rule_changes(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)
    placed = await submit_order(db_session, session_id, tax_rate=TAX)

    await db_session.execute(
        update(ComboRule).values(
            discount_cents=500, is_active=False, name_i18n={"en": "Renamed"}
        )
    )

    assert await get_order(db_session, placed.order_id) == placed


async def test_an_order_keeps_its_discount_when_the_rule_is_deleted(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)
    placed = await submit_order(db_session, session_id, tax_rate=TAX)

    for rule in (await db_session.execute(select(ComboRule))).scalars():
        await db_session.delete(rule)
    await db_session.flush()

    assert (await get_order(db_session, placed.order_id)).discounts == [ONE_COMBO]


async def test_an_order_freezes_the_name_in_the_language_it_was_placed_in(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await set_language(db_session, session_id, Language.FR)
    await add_a_full_combo(db_session, session_id)

    await submit_order(db_session, session_id, tax_rate=TAX)

    row = (await db_session.execute(select(OrderDiscount))).scalar_one()
    assert (row.kind, row.code, row.name) == (
        DiscountKind.COMBO,
        "burger_combo",
        "Trio burger",
    )
    assert (row.position, row.times, row.amount_cents) == (0, 1, 200)


async def test_an_order_without_a_combo_has_no_discounts(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add(db_session, session_id, A_COOKIE)

    order = await submit_order(db_session, session_id, tax_rate=TAX)

    assert order.discounts == []
    assert order.discount_cents == 0
    assert (await db_session.execute(select(OrderDiscount))).first() is None


# --- Suggesting a combo -----------------------------------------------------


async def test_adding_a_burger_suggests_the_combo(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    outcome = await propose(db_session, session_id, A_BURGER)

    assert outcome.decision is Decision.ALLOW
    suggestion = outcome.suggestion
    assert suggestion is not None
    assert (suggestion.kind, suggestion.code) == (DiscountKind.COMBO, "burger_combo")
    assert (suggestion.name, suggestion.saves_cents) == ("Burger combo", 200)
    assert [(c.category_code, c.category_name) for c in suggestion.missing] == [
        ("sides", "Sides"),
        ("drinks", "Drinks"),
    ]
    assert outcome.message.endswith(gateway.SUGGESTION_INSTRUCTION)


async def test_the_suggestion_keeps_up_with_the_cart_during_the_turn(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "A burger and fries", sent as two calls: the second says only a drink is
    missing, so the agent offers exactly what is needed."""
    first = await propose(db_session, session_id, A_BURGER)
    second = await propose(db_session, session_id, SMALL_FRIES)

    assert missing(first) == ["sides", "drinks"]
    assert missing(second) == ["drinks"]


async def test_completing_the_combo_ends_the_suggestion(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_BURGER)
    await propose(db_session, session_id, SMALL_FRIES)

    outcome = await propose(db_session, session_id, A_WATER)

    assert outcome.suggestion is None
    assert outcome.cart.discounts == [ONE_COMBO]
    assert not outcome.message.endswith(gateway.SUGGESTION_INSTRUCTION)


async def test_a_drink_alone_suggests_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    assert (await propose(db_session, session_id, A_WATER)).suggestion is None
    assert (await propose(db_session, session_id, SMALL_FRIES)).suggestion is None


async def test_a_combo_is_never_suggested_after_the_customer_has_spoken(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_BURGER)

    await close_suggestions(db_session, session_id)

    assert (await propose(db_session, session_id, SMALL_FRIES)).suggestion is None
    assert (await propose(db_session, session_id, A_BURGER)).suggestion is None
    row = (await db_session.execute(select(SessionSuggestion))).scalar_one()
    assert (row.kind, row.code, row.status) == (
        DiscountKind.COMBO,
        "burger_combo",
        SuggestionStatus.CLOSED,
    )


async def test_a_declined_combo_still_applies_if_the_customer_builds_it(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Not suggesting it again is not the same as not giving it."""
    await propose(db_session, session_id, A_BURGER)
    await close_suggestions(db_session, session_id)

    await propose(db_session, session_id, SMALL_FRIES)
    outcome = await propose(db_session, session_id, A_WATER)

    assert outcome.cart.discounts == [ONE_COMBO]


async def test_closing_with_nothing_open_does_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await close_suggestions(db_session, session_id)

    assert missing(await propose(db_session, session_id, A_BURGER)) == [
        "sides",
        "drinks",
    ]


async def test_each_session_gets_its_own_suggestion(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    other = (await open_session(db_session, "lane-2")).id
    await propose(db_session, session_id, A_BURGER)
    await close_suggestions(db_session, session_id)

    assert (await propose(db_session, other, A_BURGER)).suggestion is not None


async def test_a_combo_that_cannot_be_completed_is_not_suggested(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    drinks = await db_session.execute(
        select(MenuItem.code)
        .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
        .where(MenuCategory.code == "drinks")
    )
    for code in drinks.scalars().all():
        await set_on_hand(db_session, code, 0)

    assert (await propose(db_session, session_id, A_BURGER)).suggestion is None


async def test_one_drink_left_is_enough_to_suggest(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    drinks = await db_session.execute(
        select(MenuItem.code)
        .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
        .where(MenuCategory.code == "drinks", MenuItem.code != "coffee")
    )
    for code in drinks.scalars().all():
        await set_on_hand(db_session, code, 0)

    assert (await propose(db_session, session_id, A_BURGER)).suggestion is not None


async def test_a_refused_addition_suggests_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_BURGER)

    no_size = await propose(db_session, session_id, AddItem(item_code="fries"))

    assert no_size.decision is Decision.REJECT
    assert no_size.suggestion is None


async def test_taking_something_off_suggests_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Offering a combo to someone who has just removed their drink is the
    wrong moment."""
    await add_a_full_combo(db_session, session_id)

    removed = await propose(db_session, session_id, RemoveLine(line_no=3))
    changed = await propose(db_session, session_id, UpdateLine(line_no=1, quantity=2))

    assert removed.suggestion is None
    assert changed.suggestion is None
    assert (await db_session.execute(select(SessionSuggestion))).first() is None


async def test_a_second_burger_opens_the_combo_again_in_the_same_turn(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_a_full_combo(db_session, session_id)

    outcome = await propose(db_session, session_id, A_BURGER)

    assert missing(outcome) == ["sides", "drinks"]
    assert outcome.cart.discounts == [ONE_COMBO]


async def test_the_suggestion_is_in_the_customers_language(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await set_language(db_session, session_id, Language.FR)

    suggestion = (await propose(db_session, session_id, A_BURGER)).suggestion

    assert suggestion is not None
    assert suggestion.name == "Trio burger"
    assert [c.category_name for c in suggestion.missing] == [
        "Accompagnements",
        "Boissons",
    ]


async def test_a_repeated_request_gets_the_same_suggestion_back(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    first = await propose(db_session, session_id, A_BURGER, key="call-1")
    await close_suggestions(db_session, session_id)

    replay = await propose(db_session, session_id, A_BURGER, key="call-1")

    assert replay == first
    assert replay.suggestion is not None
    assert replay.cart.item_count == 1


async def test_no_combos_means_no_suggestions(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(update(ComboRule).values(is_active=False))

    assert (await propose(db_session, session_id, A_BURGER)).suggestion is None


# --- The agent's turns ------------------------------------------------------

AGENT = AgentConfig(
    restaurant_name="Maple Lane Burgers", tax_rate=TAX, limits=LIMITS, max_tool_rounds=4
)
BURGER_ARGS = {"item_code": "classic_burger", "quantity": 1, "selections": []}
FRIES_ARGS = {
    "item_code": "fries",
    "quantity": 1,
    "selections": [{"group": "size", "options": ["small"]}],
}
WATER_ARGS = {"item_code": "bottled_water", "quantity": 1, "selections": []}


async def tool_results(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        message.result
        for message in await events.load_messages(db, session_id)
        if isinstance(message, ToolResult)
    ]


async def test_the_agent_is_told_about_the_combo_in_one_turn_only(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    offer = ScriptedModel(
        [call("add_item", **BURGER_ARGS), say("Make it a combo and save two dollars?")]
    )
    declined = ScriptedModel(
        [call("add_item", **FRIES_ARGS), say("Fries added. Anything else?")]
    )

    await run_turn(db_session, session_id, "A burger", model=offer, config=AGENT)
    await run_turn(
        db_session, session_id, "No, just add fries", model=declined, config=AGENT
    )

    first, second = await tool_results(db_session, session_id)
    assert first["suggestion"]["code"] == "burger_combo"
    assert "suggestion" not in second


async def test_within_a_turn_the_agent_sees_the_latest_suggestion(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            calls(
                ToolCall("a", "add_item", BURGER_ARGS),
                ToolCall("b", "add_item", FRIES_ARGS),
            ),
            say("Add a drink to make it a combo?"),
        ]
    )

    await run_turn(
        db_session, session_id, "A burger and fries", model=model, config=AGENT
    )

    first, second = await tool_results(db_session, session_id)
    assert [c["category_code"] for c in first["suggestion"]["missing"]] == [
        "sides",
        "drinks",
    ]
    assert [c["category_code"] for c in second["suggestion"]["missing"]] == ["drinks"]


async def test_accepting_the_combo_shows_the_saving_in_the_reply_cart(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    order = ScriptedModel(
        [
            calls(
                ToolCall("a", "add_item", BURGER_ARGS),
                ToolCall("b", "add_item", FRIES_ARGS),
            ),
            say("Add a drink to make it a combo?"),
        ]
    )
    accept = ScriptedModel(
        [call("add_item", **WATER_ARGS), say("That's a combo. You save two dollars.")]
    )

    await run_turn(
        db_session, session_id, "A burger and fries", model=order, config=AGENT
    )
    reply = await run_turn(
        db_session, session_id, "Sure, a water", model=accept, config=AGENT
    )

    assert reply.cart.discounts == [ONE_COMBO]
    assert reply.cart.total_cents == 844
    last = (await tool_results(db_session, session_id))[-1]
    assert "suggestion" not in last
    assert last["cart"]["discount_cents"] == 200


# --- Over HTTP --------------------------------------------------------------


@pytest.fixture
async def http_session(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


async def post_line(
    client: httpx.AsyncClient, session_id: str, action: AddItem
) -> dict[str, Any]:
    response = await client.post(
        f"/sessions/{session_id}/cart/lines",
        json={"item_code": action.item_code, "selections": action.selections},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_the_combos_are_listed(client: httpx.AsyncClient) -> None:
    response = await client.get("/combos")

    assert response.status_code == 200
    assert response.json() == [
        {
            "code": "burger_combo",
            "name": "Burger combo",
            "slots": [
                {"category_code": "burgers", "category_name": "Burgers"},
                {"category_code": "sides", "category_name": "Sides"},
                {"category_code": "drinks", "category_name": "Drinks"},
            ],
            "saves_cents": 200,
        }
    ]


async def test_the_combos_are_listed_in_french(client: httpx.AsyncClient) -> None:
    (combo,) = (await client.get("/combos", params={"language": "fr"})).json()

    assert combo["name"] == "Trio burger"
    assert [slot["category_name"] for slot in combo["slots"]] == [
        "Hamburgers",
        "Accompagnements",
        "Boissons",
    ]


async def test_retired_combos_are_not_listed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(update(ComboRule).values(is_active=False))

    assert (await client.get("/combos")).json() == []
    assert await list_combos(db_session, Language.EN) == []


async def test_the_cart_endpoints_show_the_discount(
    client: httpx.AsyncClient, http_session: str
) -> None:
    await post_line(client, http_session, A_BURGER)
    before = await post_line(client, http_session, SMALL_FRIES)
    after = await post_line(client, http_session, A_WATER)

    assert (before["discounts"], before["discount_cents"]) == ([], 0)
    assert after["discounts"] == [ONE_COMBO.model_dump(mode="json")]
    assert (after["discount_cents"], after["total_cents"]) == (200, 844)
    assert (await client.get(f"/sessions/{http_session}/cart")).json() == after


async def test_an_order_placed_over_http_carries_its_discount(
    client: httpx.AsyncClient, http_session: str
) -> None:
    for action in (A_BURGER, SMALL_FRIES, A_WATER):
        await post_line(client, http_session, action)

    placed = (await client.post(f"/sessions/{http_session}/order")).json()
    read_back = (await client.get(f"/orders/{placed['order_id']}")).json()

    assert placed["discounts"] == [ONE_COMBO.model_dump(mode="json")]
    assert (placed["discount_cents"], placed["total_cents"]) == (200, 844)
    assert read_back == placed


async def test_a_proposed_action_reports_the_suggestion(
    client: httpx.AsyncClient, http_session: str
) -> None:
    response = await client.post(
        f"/sessions/{http_session}/actions",
        json={"type": "add_item", "item_code": "classic_burger"},
    )

    outcome = response.json()
    assert outcome["decision"] == "allow"
    assert outcome["suggestion"] == {
        "kind": "combo",
        "code": "burger_combo",
        "name": "Burger combo",
        "missing": [
            {"category_code": "sides", "category_name": "Sides"},
            {"category_code": "drinks", "category_name": "Drinks"},
        ],
        "saves_cents": 200,
    }


async def test_nothing_in_a_request_can_set_a_discount(
    client: httpx.AsyncClient, http_session: str
) -> None:
    for body in (
        {"type": "add_item", "item_code": "cookie", "discount_cents": 100},
        {"type": "apply_combo", "code": "burger_combo"},
    ):
        response = await client.post(f"/sessions/{http_session}/actions", json=body)
        assert response.status_code == 422

    line = await client.post(
        f"/sessions/{http_session}/cart/lines",
        json={"item_code": "cookie", "discount_cents": 100},
    )
    assert line.status_code == 422
    cart = (await client.get(f"/sessions/{http_session}/cart")).json()
    assert cart["lines"] == []
