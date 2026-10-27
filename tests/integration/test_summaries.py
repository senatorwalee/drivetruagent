"""Session summaries against a real database and the real menu.

Conversations are run through the agent loop with a scripted model, so the
event log is the real thing; the summary model is scripted too. Two cookies
are 298, and 337 with 13% tax.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import summarizer
from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.messages import ToolCall, UserMessage
from drivetruagent.agent.model import ModelTurn, ModelUsage
from drivetruagent.agent.scripted import ScriptedModel, call, say
from drivetruagent.agent.summary import SummarySettings
from drivetruagent.api.summaries import get_summary_model
from drivetruagent.db.models import CustomerSession, SessionSummary
from drivetruagent.enums import Channel, Mood, SessionState, SummaryAuthor
from drivetruagent.ordering.errors import SessionInProgress, SessionNotFound
from drivetruagent.ordering.sessions import end_session, open_session
from drivetruagent.policy import gateway
from drivetruagent.policy.decisions import PolicyLimits

TAX = Decimal("0.13")
AGENT = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=TAX,
    limits=PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
    ),
    max_tool_rounds=4,
)
SETTINGS = SummarySettings()

pytestmark = pytest.mark.usefixtures("menu")


async def open_car(db: AsyncSession, lane: str = "lane-1") -> uuid.UUID:
    session_id = (await open_session(db, lane)).id
    await db.commit()
    return session_id


async def turn(
    db: AsyncSession, session_id: uuid.UUID, text: str, *turns: ModelTurn
) -> None:
    await run_turn(db, session_id, text, model=ScriptedModel(list(turns)), config=AGENT)
    await db.commit()


async def order_two_cookies(db: AsyncSession, lane: str = "lane-1") -> uuid.UUID:
    session_id = await open_car(db, lane)
    await turn(
        db,
        session_id,
        "Two chocolate chip cookies, that's all",
        call("add_item", item_code="cookie", quantity=2, selections=[]),
        call("request_confirmation", "c2"),
        say("Two chocolate chip cookies, $3.37. Is that right?"),
    )
    await turn(
        db, session_id, "Yes", call("submit_order"), say("Thanks! Pull forward.")
    )
    return session_id


def summary_model(**arguments: Any) -> ScriptedModel:
    return ScriptedModel(
        [
            ModelTurn(
                tool_calls=(ToolCall("s1", "write_summary", arguments),),
                model="summary-model",
                usage=ModelUsage(input_tokens=900, output_tokens=40),
            )
        ]
    )


async def summary_of(db: AsyncSession, session_id: uuid.UUID) -> SessionSummary:
    return (
        await db.execute(
            select(SessionSummary)
            .where(SessionSummary.session_id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# --- Writing ----------------------------------------------------------------


async def test_a_faithful_summary_from_the_model_is_kept(
    db_session: AsyncSession,
) -> None:
    session_id = await order_two_cookies(db_session)
    model = summary_model(
        gist="Ordered two chocolate chip cookies for $3.37.",
        follow_up="",
        mood="pleased",
    )

    stored = await summarizer.summarize(
        db_session, session_id, model, settings=SETTINGS
    )

    assert stored
    summary = await summary_of(db_session, session_id)
    assert summary.written_by is SummaryAuthor.MODEL
    assert summary.gist == "Ordered two chocolate chip cookies for $3.37."
    assert (summary.follow_up, summary.mood) == (None, Mood.PLEASED)
    assert (summary.model, summary.input_tokens, summary.output_tokens) == (
        "summary-model",
        900,
        40,
    )
    assert summary.rejected_reason is None
    facts = summary.facts
    assert facts["outcome"] == "submitted"
    assert facts["order"]["total_cents"] == 337
    assert facts["order"]["lines"] == [
        {"quantity": 2, "item_name": "Chocolate chip cookie"}
    ]
    assert facts["customer_turns"] == 2
    # The model was shown the conversation and the facts, and the one tool.
    request = model.requests[0]
    assert [tool.name for tool in request.tools] == ["write_summary"]
    prompt = request.messages[0]
    assert isinstance(prompt, UserMessage)
    assert "Customer: Two chocolate chip cookies, that's all" in prompt.text
    assert '"total_cents":337' in prompt.text


async def test_words_that_name_an_item_nobody_ordered_are_replaced(
    db_session: AsyncSession,
) -> None:
    session_id = await order_two_cookies(db_session)
    model = summary_model(gist="Ordered a fish burger.", follow_up="", mood="neutral")

    await summarizer.summarize(db_session, session_id, model, settings=SETTINGS)

    summary = await summary_of(db_session, session_id)
    assert summary.written_by is SummaryAuthor.TEMPLATE
    assert summary.rejected_reason == "item_not_in_conversation: Fish burger"
    assert summary.gist.startswith("Ordered: 2 × Chocolate chip cookie, $3.37 (order #")  # noqa: RUF001
    assert summary.mood is None
    # What the refused words cost is still recorded.
    assert summary.input_tokens == 900


async def test_a_follow_up_gets_the_same_checks(db_session: AsyncSession) -> None:
    session_id = await order_two_cookies(db_session)
    model = summary_model(
        gist="Ordered two cookies.",
        follow_up="Call them back on 416 555 0123.",
        mood="neutral",
    )

    await summarizer.summarize(db_session, session_id, model, settings=SETTINGS)

    summary = await summary_of(db_session, session_id)
    assert summary.rejected_reason == "phone_number"
    assert summary.follow_up is None


async def test_a_model_that_fails_leaves_code_s_own_words(
    db_session: AsyncSession,
) -> None:
    session_id = await order_two_cookies(db_session)

    class Broken:
        async def complete(self, **_: Any) -> ModelTurn:
            raise RuntimeError("down")

    await summarizer.summarize(db_session, session_id, Broken(), settings=SETTINGS)

    summary = await summary_of(db_session, session_id)
    assert (summary.written_by, summary.rejected_reason) == (
        SummaryAuthor.TEMPLATE,
        "model_failed",
    )


async def test_without_a_model_code_writes_it(db_session: AsyncSession) -> None:
    session_id = await order_two_cookies(db_session)

    await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)

    summary = await summary_of(db_session, session_id)
    assert (summary.written_by, summary.rejected_reason) == (
        SummaryAuthor.TEMPLATE,
        "no_model",
    )


async def test_a_session_nobody_spoke_in_is_never_sent_to_the_model(
    db_session: AsyncSession,
) -> None:
    session_id = await open_car(db_session)
    await end_session(db_session, session_id, SessionState.ABANDONED, "silence")
    await db_session.commit()
    model = ScriptedModel([])

    await summarizer.summarize(db_session, session_id, model, settings=SETTINGS)

    assert model.requests == []
    summary = await summary_of(db_session, session_id)
    assert summary.gist == "Nobody spoke, and the session timed out."
    assert summary.rejected_reason is None


async def test_a_handoff_s_reason_is_in_the_facts(db_session: AsyncSession) -> None:
    session_id = await open_car(db_session)
    await turn(
        db_session,
        session_id,
        "Can I talk to a person?",
        call("request_human", reason="wants to pay by cheque"),
        say("A team member will help you."),
    )

    await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)

    summary = await summary_of(db_session, session_id)
    assert summary.facts["handoff"] == {
        "reason": "human_requested",
        "title": "A person was asked for",
    }
    assert summary.gist == "Handed to a person: a person was asked for."


async def test_a_summary_is_written_once_unless_asked_again(
    db_session: AsyncSession,
) -> None:
    session_id = await order_two_cookies(db_session)
    await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)

    again = await summarizer.summarize(
        db_session,
        session_id,
        summary_model(gist="Two cookies.", follow_up="", mood="neutral"),
        settings=SETTINGS,
    )

    assert not again
    assert (
        await summary_of(db_session, session_id)
    ).written_by is SummaryAuthor.TEMPLATE

    rewritten = await summarizer.summarize(
        db_session,
        session_id,
        summary_model(gist="Two cookies.", follow_up="", mood="neutral"),
        settings=SETTINGS,
        rewrite=True,
    )

    assert rewritten
    summary = await summary_of(db_session, session_id)
    assert (summary.written_by, summary.gist) == (SummaryAuthor.MODEL, "Two cookies.")
    count = await db_session.scalar(select(func.count()).select_from(SessionSummary))
    assert count == 1


async def test_a_session_still_going_has_nothing_to_summarise(
    db_session: AsyncSession,
) -> None:
    session_id = await open_car(db_session)
    with pytest.raises(SessionInProgress):
        await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)
    with pytest.raises(SessionNotFound):
        await summarizer.summarize(db_session, uuid.uuid4(), None, settings=SETTINGS)


# --- Which sessions are waiting ---------------------------------------------


async def test_waiting_sessions_are_the_recently_ended_unsummarised_ones(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    first = await order_two_cookies(db_session, "lane-1")
    second = await order_two_cookies(db_session, "lane-2")
    old = await order_two_cookies(db_session, "lane-3")
    await open_car(db_session, "lane-4")  # still going
    await db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.id == old)
        .values(ended_at=now - timedelta(days=2))
    )
    await summarizer.summarize(db_session, second, None, settings=SETTINGS)

    waiting = await summarizer.pending(
        db_session,
        now=now + timedelta(seconds=1),
        lookback=timedelta(hours=24),
        limit=10,
    )

    assert waiting == [first]


# --- Reading, and the API ---------------------------------------------------


async def test_the_list_is_newest_first_and_filters_by_outcome(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    ordered = await order_two_cookies(db_session, "lane-1")
    left = await open_car(db_session, "lane-2")
    await turn(db_session, left, "hmm", say("Take your time."))
    await gateway.abandon(db_session, left, tax_rate=TAX)
    await db_session.commit()
    for session_id in (ordered, left):
        await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)

    everything = (await client.get("/summaries")).json()
    abandoned = (await client.get("/summaries", params={"outcome": "abandoned"})).json()

    assert [row["session_id"] for row in everything] == [str(left), str(ordered)]
    first_row = everything[1]
    assert first_row["outcome"] == "submitted"
    assert first_row["channel"] == Channel.DRIVE_THRU
    assert first_row["total_cents"] == 337
    assert isinstance(first_row["order_number"], int)
    assert first_row["written_by"] == "template"
    assert [row["session_id"] for row in abandoned] == [str(left)]
    assert abandoned[0]["gist"] == "Left without ordering."


async def test_one_summary_comes_with_its_facts(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    session_id = await order_two_cookies(db_session)
    missing = await client.get(f"/sessions/{session_id}/summary")
    await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)

    found = await client.get(f"/sessions/{session_id}/summary")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "summary_not_found"
    body = found.json()
    assert body["facts"]["order"]["total_cents"] == 337
    assert body["rejected_reason"] == "no_model"


async def test_staff_can_have_a_summary_written_again(
    db_session: AsyncSession, client: httpx.AsyncClient, app: FastAPI
) -> None:
    session_id = await order_two_cookies(db_session)
    await summarizer.summarize(db_session, session_id, None, settings=SETTINGS)
    app.dependency_overrides[get_summary_model] = lambda: summary_model(
        gist="Two chocolate chip cookies, no fuss.", follow_up="", mood="pleased"
    )

    response = await client.post(f"/sessions/{session_id}/summary")

    assert response.status_code == 200
    body = response.json()
    assert (body["written_by"], body["gist"], body["mood"]) == (
        "model",
        "Two chocolate chip cookies, no fuss.",
        "pleased",
    )


async def test_a_session_still_going_cannot_be_summarised_through_the_api(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    session_id = await open_car(db_session)

    going = await client.post(f"/sessions/{session_id}/summary")
    unknown = await client.post(f"/sessions/{uuid.uuid4()}/summary")

    assert going.status_code == 409
    assert going.json()["error"]["code"] == "session_in_progress"
    assert unknown.status_code == 404
