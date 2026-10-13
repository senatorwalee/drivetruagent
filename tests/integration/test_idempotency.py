"""A request sent twice must take effect once."""

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.messages import ToolCall
from drivetruagent.agent.scripted import ScriptedModel, calls, say
from drivetruagent.db.models import IdempotencyRecord, Order
from drivetruagent.ordering.cart import get_cart
from drivetruagent.ordering.errors import IdempotencyKeyReused
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import (
    Action,
    AddItem,
    RequestConfirmation,
    SubmitOrder,
)
from drivetruagent.policy.decisions import Decision, PolicyLimits
from drivetruagent.policy.outcome import ActionOutcome
from drivetruagent.seed import load_menu, seed_menu
from tests.integration.conftest import MENU_PATH

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
A_COOKIE = AddItem(item_code="cookie")


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


async def propose(
    db: AsyncSession, session_id: uuid.UUID, action: Action, key: str | None
) -> ActionOutcome:
    return await gateway.execute(
        db, session_id, action, tax_rate=TAX, limits=LIMITS, idempotency_key=key
    )


async def records(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(IdempotencyRecord))
    ).scalar_one()


# --- The gateway ------------------------------------------------------------


async def test_a_repeat_with_the_same_key_changes_nothing_and_answers_the_same(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    first = await propose(db_session, session_id, A_COOKIE, "key-1")

    second = await propose(db_session, session_id, A_COOKIE, "key-1")

    assert second == first
    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.item_count == 1
    assert cart.version == 1
    assert await records(db_session) == 1


async def test_without_a_key_each_request_runs(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_COOKIE, None)
    await propose(db_session, session_id, A_COOKIE, None)

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.item_count == 2
    assert await records(db_session) == 0


async def test_different_keys_are_different_requests(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_COOKIE, "key-1")
    await propose(db_session, session_id, A_COOKIE, "key-2")

    assert (await get_cart(db_session, session_id, tax_rate=TAX)).item_count == 2


async def test_a_key_cannot_be_reused_for_a_different_request(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, A_COOKIE, "key-1")

    with pytest.raises(IdempotencyKeyReused) as error:
        await propose(db_session, session_id, AddItem(item_code="apple_pie"), "key-1")

    assert error.value.code == "idempotency_key_reused"
    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert [line.item_code for line in cart.lines] == ["cookie"]


async def test_a_key_belongs_to_one_session(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    other = (await open_session(db_session, "lane-2")).id

    await propose(db_session, session_id, A_COOKIE, "same-key")
    outcome = await propose(db_session, other, A_COOKIE, "same-key")

    assert outcome.cart.session_id == other
    assert outcome.cart.item_count == 1


async def test_a_rejection_is_replayed_too(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The caller asked the same thing; it gets the same answer."""
    no_size = AddItem(item_code="fries")

    first = await propose(db_session, session_id, no_size, "key-1")
    second = await propose(db_session, session_id, no_size, "key-1")

    assert first.decision is Decision.REJECT
    assert second == first


async def test_confirming_twice_with_one_key_adds_the_items_once(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(
        db_session, session_id, AddItem(item_code="cookie", quantity=15), None
    )
    assert held.pending_action_id is not None

    async def say_yes() -> ActionOutcome:
        return await gateway.confirm(
            db_session,
            session_id,
            held.pending_action_id,  # pyright: ignore[reportArgumentType]
            tax_rate=TAX,
            limits=LIMITS,
            idempotency_key="yes-1",
        )

    first = await say_yes()
    second = await say_yes()

    assert first.decision is Decision.ALLOW
    assert second == first
    assert second.cart.item_count == 15


async def test_two_copies_of_a_request_arriving_together_take_effect_once(
    committing_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Real concurrency: both copies are in flight at the same moment."""
    async with committing_sessions.begin() as db:
        await seed_menu(db, load_menu(MENU_PATH))
        session_id = (await open_session(db)).id

    async def send() -> ActionOutcome:
        async with committing_sessions.begin() as db:
            return await propose(db, session_id, A_COOKIE, "retry-key")

    outcomes = await asyncio.gather(*(send() for _ in range(5)))

    async with committing_sessions() as db:
        cart = await get_cart(db, session_id, tax_rate=TAX)
    assert cart.item_count == 1
    assert all(outcome == outcomes[0] for outcome in outcomes)


# --- The agent's tool calls -------------------------------------------------


AGENT = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=TAX,
    limits=LIMITS,
    max_tool_rounds=4,
)
COOKIE_ARGS = {"item_code": "cookie", "quantity": 1, "selections": []}


async def test_the_same_tool_call_run_twice_adds_one_item(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """A model that sends one call twice, with the same call id."""
    model = ScriptedModel(
        [
            calls(
                ToolCall("call_a", "add_item", COOKIE_ARGS),
                ToolCall("call_a", "add_item", COOKIE_ARGS),
            ),
            say("One cookie."),
        ]
    )

    reply = await run_turn(
        db_session, session_id, "A cookie", model=model, config=AGENT
    )

    assert reply.cart.item_count == 1


async def test_two_different_tool_calls_are_two_requests(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "Two cookies" said as two separate calls is two items, as it should be."""
    model = ScriptedModel(
        [
            calls(
                ToolCall("call_a", "add_item", COOKIE_ARGS),
                ToolCall("call_b", "add_item", COOKIE_ARGS),
            ),
            say("Two cookies."),
        ]
    )

    reply = await run_turn(
        db_session, session_id, "Two cookies", model=model, config=AGENT
    )

    assert reply.cart.item_count == 2


async def test_a_call_id_reused_in_a_later_turn_is_a_new_request(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    async def say_a_cookie() -> int:
        model = ScriptedModel(
            [calls(ToolCall("call_1", "add_item", COOKIE_ARGS)), say("Added.")]
        )
        reply = await run_turn(
            db_session, session_id, "A cookie", model=model, config=AGENT
        )
        return reply.cart.item_count

    assert [await say_a_cookie(), await say_a_cookie()] == [1, 2]


# --- Over HTTP --------------------------------------------------------------


@pytest.fixture
async def http_session(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


async def test_a_retried_action_request_returns_the_same_body(
    client: httpx.AsyncClient, http_session: str
) -> None:
    send = lambda: client.post(  # noqa: E731
        f"/sessions/{http_session}/actions",
        json={"type": "add_item", "item_code": "cookie"},
        headers={"Idempotency-Key": "abc-123"},
    )

    first = await send()
    second = await send()

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    cart = (await client.get(f"/sessions/{http_session}/cart")).json()
    assert cart["item_count"] == 1


async def test_a_reused_key_with_another_body_is_refused(
    client: httpx.AsyncClient, http_session: str
) -> None:
    url = f"/sessions/{http_session}/actions"
    headers = {"Idempotency-Key": "abc-123"}
    await client.post(
        url, json={"type": "add_item", "item_code": "cookie"}, headers=headers
    )

    response = await client.post(
        url, json={"type": "add_item", "item_code": "apple_pie"}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


async def test_a_retried_order_submission_returns_the_same_order(
    client: httpx.AsyncClient, http_session: str, db_session: AsyncSession
) -> None:
    """The case the key exists for: the first response was lost on the way back."""
    await client.post(
        f"/sessions/{http_session}/cart/lines", json={"item_code": "cookie"}
    )
    headers = {"Idempotency-Key": "submit-once"}

    first = await client.post(f"/sessions/{http_session}/order", headers=headers)
    retry = await client.post(f"/sessions/{http_session}/order", headers=headers)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 1


async def test_without_a_key_a_second_submission_is_an_error(
    client: httpx.AsyncClient, http_session: str
) -> None:
    await client.post(
        f"/sessions/{http_session}/cart/lines", json={"item_code": "cookie"}
    )

    first = await client.post(f"/sessions/{http_session}/order")
    second = await client.post(f"/sessions/{http_session}/order")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "session_ended"


async def test_the_whole_agent_flow_can_be_retried_step_by_step(
    client: httpx.AsyncClient, http_session: str, db_session: AsyncSession
) -> None:
    """Every step sent twice, as an unreliable network would."""
    steps: list[tuple[str, dict[str, object]]] = [
        ("step-1", AddItem(item_code="cookie", quantity=2).model_dump()),
        ("step-2", RequestConfirmation().model_dump()),
        ("step-3", SubmitOrder().model_dump()),
    ]

    for key, body in steps:
        for _ in range(2):
            response = await client.post(
                f"/sessions/{http_session}/actions",
                json=body,
                headers={"Idempotency-Key": key},
            )
            assert response.json()["decision"] == "allow"

    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.total_cents == 337  # two cookies, 298 + 13%
