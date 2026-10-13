"""The scripted demo model, played through the real loop, tools and policy.

Only the model is scripted. These tests check that the conversation it plays
really does what the demo claims: a combo suggested and earned, a refusal, a
held action, a cancellation, a read-back and an order.
"""

import uuid
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.demo_model import FINISHED, SUGGESTED_LINES, DemoModel
from drivetruagent.agent.loop import AgentConfig, AgentReply, run_turn
from drivetruagent.agent.messages import ToolResult
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import ComboRule
from drivetruagent.enums import SessionState
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits

CONFIG = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=Decimal("0.13"),
    limits=PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
    ),
    max_tool_rounds=8,
)


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


async def play(db: AsyncSession, session_id: uuid.UUID, turns: int) -> list[AgentReply]:
    return [
        await run_turn(db, session_id, line, model=DemoModel(), config=CONFIG)
        for line in SUGGESTED_LINES[:turns]
    ]


async def decisions(db: AsyncSession, session_id: uuid.UUID) -> list[tuple[str, str]]:
    """(tool, decision or error code) for every tool result, in order."""
    return [
        (m.name, m.result.get("decision") or m.result["error"]["code"])
        for m in await events.load_messages(db, session_id)
        if isinstance(m, ToolResult)
    ]


async def test_the_whole_demo_ends_with_the_right_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    replies = await play(db_session, session_id, turns=8)

    final = replies[-1]
    assert [reply.cart.state for reply in replies] == [
        SessionState.GREETING,
        SessionState.ORDERING,
        SessionState.ORDERING,  # the fries were refused: no size
        SessionState.ORDERING,
        SessionState.ORDERING,  # fifteen cookies held, nothing added
        SessionState.ORDERING,
        SessionState.CONFIRMING,
        SessionState.SUBMITTED,
    ]
    assert [(line.item_code, line.quantity) for line in final.cart.lines] == [
        ("cheeseburger", 1),
        ("fries", 1),
        ("cola", 1),
        ("cookie", 2),
    ]
    # 599 + 379 + 239 + 298 = 1515, less the 200 combo = 1315, plus 13% = 1486.
    assert final.cart.discount_cents == 200
    assert final.cart.total_cents == 1486


async def test_the_demo_shows_every_kind_of_decision_it_claims_to(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await play(db_session, session_id, turns=8)

    assert await decisions(db_session, session_id) == [
        ("add_item", "allow"),  # cheeseburger
        ("add_item", "reject"),  # fries with no size
        ("add_item", "allow"),  # large fries
        ("add_item", "allow"),  # medium cola
        ("add_item", "confirm"),  # fifteen cookies
        ("cancel_pending_action", "allow"),
        ("add_item", "allow"),  # two cookies
        ("request_confirmation", "allow"),
        ("submit_order", "allow"),
    ]


async def test_fifteen_cookies_are_held_and_not_added(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    replies = await play(db_session, session_id, turns=5)

    assert replies[-1].text == "Fifteen cookies, is that right?"
    assert [line.item_code for line in replies[-1].cart.lines] == [
        "cheeseburger",
        "fries",
        "cola",
    ]


async def test_the_combo_is_offered_because_the_backend_suggested_it(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    replies = await play(db_session, session_id, turns=2)

    assert replies[-1].text == (
        "One cheeseburger. Add a side and a drink to make it a burger combo "
        "and save $2.00?"
    )
    assert replies[-1].cart.discounts == []


async def test_the_saving_is_announced_when_the_combo_is_complete(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    replies = await play(db_session, session_id, turns=4)

    assert replies[-1].text == (
        "Large fries and a medium cola. That makes it a burger combo, so you "
        "save $2.00. Anything else?"
    )
    assert replies[-1].cart.discount_cents == 200
    # The combo was offered in turn two and not pushed again in turn three.
    assert "combo" not in replies[2].text


async def test_with_no_combos_the_demo_still_runs_and_offers_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(update(ComboRule).values(is_active=False))
    await db_session.commit()

    replies = await play(db_session, session_id, turns=8)

    assert replies[1].text == "One cheeseburger. Anything else?"
    assert replies[3].text == "Large fries and a medium cola. Anything else?"
    assert "saved" not in replies[6].text
    assert replies[-1].cart.total_cents == 1712  # 1515 + 13%


async def test_what_the_model_says_about_money_comes_from_the_tools(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    replies = await play(db_session, session_id, turns=8)

    read_back, placed = replies[6].text, replies[7].text
    assert "$14.86" in read_back
    assert "You saved $2.00." in read_back
    assert "1 cheeseburger, 1 fries, 1 cola, 2 chocolate chip cookie" in read_back
    assert "$14.86" in placed
    assert "order number is" in placed


async def test_after_the_script_ends_it_says_so(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await play(db_session, session_id, turns=8)

    extra = await run_turn(
        db_session, session_id, "One more thing", model=DemoModel(), config=CONFIG
    )

    assert extra.text == FINISHED
    assert extra.tools_used == ()


def test_there_is_a_suggested_line_for_every_turn() -> None:
    assert len(SUGGESTED_LINES) == 8


# --- Through the API, selected by a setting ---------------------------------


@pytest.fixture
def demo_app(app: FastAPI) -> FastAPI:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        agent_model="demo",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    return app


async def test_the_demo_runs_through_the_api_with_no_key(
    demo_app: FastAPI, client: httpx.AsyncClient, menu: None
) -> None:
    session_id = (await client.post("/sessions")).json()["session_id"]

    replies = [
        (
            await client.post(f"/sessions/{session_id}/messages", json={"text": line})
        ).json()
        for line in SUGGESTED_LINES
    ]

    assert replies[-1]["cart"]["state"] == "submitted"
    assert replies[-1]["cart"]["total_cents"] == 1486
    assert replies[-1]["cart"]["discount_cents"] == 200
    assert replies[3]["tools_used"] == ["add_item", "add_item"]


async def test_agent_info_describes_the_demo_model(
    demo_app: FastAPI, client: httpx.AsyncClient
) -> None:
    info = (await client.get("/agent")).json()

    assert info == {
        "restaurant_name": "Maple Lane Burgers",
        "available": True,
        "model": "demo-script",
        "scripted": True,
        "suggested_lines": list(SUGGESTED_LINES),
    }
