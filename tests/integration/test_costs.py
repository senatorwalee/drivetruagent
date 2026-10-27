"""What the model cost, from the event log and the shipped price list.

Model calls are written into the log directly, as gpt-6-luna, so each
figure can be worked out by hand: per million tokens it is 0.10 in, 0.01
cached in, 0.50 out, which in micro-dollars is just tokens times price.
"""

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events, summarizer
from drivetruagent.agent.costs import load_prices
from drivetruagent.agent.messages import UserMessage
from drivetruagent.agent.summary import SummarySettings
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import CustomerSession, SessionSummary
from drivetruagent.enums import AgentEventKind, Channel, SessionState, SummaryAuthor
from tests.integration.conftest import TEST_TAX_RATE
from tests.integration.factories import make_order, make_session

DAY = date(2026, 10, 27)
PRICES = load_prices()

# 1,000 in and 100 out: 100 + 50.
TURN = {"model": "gpt-6-luna", "input_tokens": 1000, "output_tokens": 100}
# 100 new and 300 cached in, 12 out: 10 + 3 + 6.
GUARD = {
    "model": "gpt-6-luna",
    "purpose": "guardrail",
    "input_tokens": 400,
    "cached_input_tokens": 300,
    "output_tokens": 12,
}
UNPRICED = {"model": "scripted", "input_tokens": 50, "output_tokens": 5}


async def session_with(
    db: AsyncSession,
    *calls: dict[str, Any],
    said: bool = True,
    channel: Channel = Channel.DRIVE_THRU,
    day: date = DAY,
) -> CustomerSession:
    customer_session = await make_session(db, business_date=day, channel=channel)
    turn = uuid.uuid4()
    if said:
        await events.record_message(db, customer_session.id, turn, UserMessage("Hi"))
    for call in calls:
        await events.record(
            db, customer_session.id, turn, AgentEventKind.MODEL_CALL, call
        )
    await db.commit()
    return customer_session


async def add_summary(db: AsyncSession, session_id: uuid.UUID) -> None:
    """A note written by gpt-6-luna: 900 in and 40 out, 90 + 20."""
    db.add(
        SessionSummary(
            session_id=session_id,
            facts={},
            gist="A note.",
            mood="neutral",
            written_by=SummaryAuthor.MODEL,
            model="gpt-6-luna",
            input_tokens=900,
            output_tokens=40,
        )
    )
    await db.commit()


async def test_a_session_s_cost_counts_every_call_it_can_price(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    customer_session = await session_with(db_session, TURN, GUARD, UNPRICED)

    before = (await client.get(f"/sessions/{customer_session.id}/cost")).json()
    await add_summary(db_session, customer_session.id)
    after = (await client.get(f"/sessions/{customer_session.id}/cost")).json()

    assert before["conversation"] == {
        "micro_usd": 150 + 19,
        "calls": 3,
        "unpriced_calls": 1,
        "estimated_calls": 0,
    }
    assert before["summary"]["micro_usd"] == 0
    assert after["summary"] == {
        "micro_usd": 110,
        "calls": 1,
        "unpriced_calls": 0,
        "estimated_calls": 0,
    }


async def test_an_unknown_session_has_no_cost(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/sessions/{uuid.uuid4()}/cost")
    assert response.status_code == 404


async def test_the_day_adds_up_per_conversation_and_per_order(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ordered = await session_with(db_session, TURN, GUARD)
    await make_order(db_session, ordered)
    await add_summary(db_session, ordered.id)
    await session_with(db_session, TURN, channel=Channel.PHONE)
    # Nobody spoke: not a conversation, and it cost nothing.
    await session_with(db_session, said=False)
    # Another day.
    await session_with(db_session, TURN, TURN, day=date(2026, 10, 26))

    day = (await client.get("/costs/today", params={"day": DAY.isoformat()})).json()

    assert (day["business_date"], day["conversations"], day["orders"]) == (
        "2026-10-27",
        2,
        1,
    )
    # Turns 150 + 150, the guardrail 19, the note 110.
    assert day["total"]["micro_usd"] == 429
    assert {
        purpose: cost["micro_usd"] for purpose, cost in day["by_purpose"].items()
    } == {
        "turn": 300,
        "guardrail": 19,
        "summary": 110,
    }
    assert {
        channel: cost["micro_usd"] for channel, cost in day["by_channel"].items()
    } == {
        "drive_thru": 169,
        "phone": 150,
    }
    assert day["per_conversation_micro_usd"] == 214  # 429 / 2, rounded
    assert day["per_order_micro_usd"] == 429
    assert day["prices_checked_on"] == PRICES.checked_on.isoformat()


async def test_a_day_with_nothing_has_nothing_to_divide(
    client: httpx.AsyncClient,
) -> None:
    day = (await client.get("/costs/today", params={"day": "2020-01-01"})).json()

    assert (day["conversations"], day["orders"], day["total"]["micro_usd"]) == (0, 0, 0)
    assert day["per_conversation_micro_usd"] is None
    assert day["per_order_micro_usd"] is None


async def test_without_a_price_list_costs_are_unavailable(
    app: FastAPI, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        tax_rate=TEST_TAX_RATE,
        model_prices_path=tmp_path / "missing.json",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )

    response = await client.get("/costs/today")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_prices"


async def test_a_summary_keeps_what_its_conversation_cost(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    customer_session = await session_with(db_session, TURN, GUARD)
    customer_session.state = SessionState.ABANDONED
    customer_session.ended_at = customer_session.started_at
    await db_session.commit()

    await summarizer.summarize(
        db_session, customer_session.id, None, settings=SummarySettings(prices=PRICES)
    )
    body = (await client.get(f"/sessions/{customer_session.id}/summary")).json()
    listed = (await client.get("/summaries")).json()

    assert body["facts"]["cost"]["micro_usd"] == 169
    assert body["cost"]["micro_usd"] == 169
    # Written by code: no model asked, nothing to price.
    assert body["summary_cost"] is None
    assert listed[0]["cost"]["micro_usd"] == 169
