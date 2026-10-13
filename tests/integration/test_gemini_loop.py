"""The Gemini adapter driven by the real loop, tools and event log.

Only Google's service is replaced, by a stand-in that answers with the SDK's
own response types. What is under test is the part that could not be checked
with pure functions: that what Gemini attaches to a call is stored in the
event log and comes back on the next request, in the shape Gemini wants.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.gemini_model import GeminiModel
from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.messages import ToolCall
from drivetruagent.db.models import AgentEvent
from drivetruagent.enums import AgentEventKind
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits

SIGNATURE = b"\x00reasoning\xff"
CONFIG = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=Decimal("0.13"),
    limits=PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
    ),
    max_tool_rounds=4,
)

pytestmark = pytest.mark.usefixtures("menu")


class StandInGoogle:
    """Answers each request with the next scripted response, and keeps the
    requests it was sent."""

    def __init__(self, *answers: types.GenerateContentResponse) -> None:
        self.requests: list[dict[str, Any]] = []
        self._answers = list(answers)
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._send))

    async def _send(self, **request: Any) -> types.GenerateContentResponse:
        self.requests.append(request)
        return self._answers.pop(0)


def answer(*parts: types.Part, response_id: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=list(parts)))
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=900, candidates_token_count=20
        ),
        model_version="gemini-2.5-flash-lite",
        response_id=response_id,
    )


def add(
    item_code: str, *, signature: bytes | None = None, **choices: str
) -> types.Part:
    arguments = {
        "item_code": item_code,
        "quantity": 1,
        "selections": [{"group": g, "options": [o]} for g, o in choices.items()],
    }
    return types.Part(
        function_call=types.FunctionCall(name="add_item", args=arguments),
        thought_signature=signature,
    )


def say(text: str, response_id: str) -> types.GenerateContentResponse:
    return answer(types.Part(text=text), response_id=response_id)


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


def model_over(google: StandInGoogle) -> GeminiModel:
    return GeminiModel(api_key="unused", model="gemini-2.5-flash-lite", client=google)  # pyright: ignore[reportArgumentType]


def shape(contents: list[types.Content]) -> list[tuple[str | None, list[str]]]:
    def describe(part: types.Part) -> str:
        if part.function_call:
            return f"call:{part.function_call.args['item_code']}"  # pyright: ignore[reportOptionalSubscript]
        if part.function_response:
            return f"result:{part.function_response.response['decision']}"  # pyright: ignore[reportOptionalSubscript]
        return "text"

    return [(c.role, [describe(p) for p in c.parts or []]) for c in contents]


async def test_two_items_asked_for_at_once_are_both_added(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    google = StandInGoogle(
        answer(
            add("fries", signature=SIGNATURE, size="large"),
            add("cola", size="medium"),
            response_id="resp-1",
        ),
        say("Large fries and a medium cola. Anything else?", "resp-2"),
    )

    reply = await run_turn(
        db_session,
        session_id,
        "Large fries and a medium cola",
        model=model_over(google),
        config=CONFIG,
    )

    assert reply.text == "Large fries and a medium cola. Anything else?"
    assert reply.tools_used == ("add_item", "add_item")
    assert [line.item_code for line in reply.cart.lines] == ["fries", "cola"]
    assert reply.cart.subtotal_cents == 379 + 239


async def test_the_second_request_sends_both_calls_back_together_with_the_signature(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The log holds call, result, call, result. Gemini is sent one model turn
    with both calls and one user turn with both results, and the signature
    that came with the first call goes back with it."""
    google = StandInGoogle(
        answer(
            add("fries", signature=SIGNATURE, size="large"),
            add("cola", size="medium"),
            response_id="resp-1",
        ),
        say("Done.", "resp-2"),
    )

    await run_turn(
        db_session,
        session_id,
        "Fries and a cola",
        model=model_over(google),
        config=CONFIG,
    )

    second = google.requests[1]["contents"]
    assert shape(second) == [
        ("user", ["text"]),
        ("model", ["call:fries", "call:cola"]),
        ("user", ["result:allow", "result:allow"]),
    ]
    first_call, second_call = second[1].parts
    assert first_call.thought_signature == SIGNATURE
    assert second_call.thought_signature is None


async def test_what_gemini_attached_to_a_call_is_kept_in_the_event_log(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    google = StandInGoogle(
        answer(add("cookie", signature=SIGNATURE), response_id="resp-1"),
        say("One cookie.", "resp-2"),
    )

    await run_turn(
        db_session, session_id, "A cookie", model=model_over(google), config=CONFIG
    )

    stored = (
        await db_session.execute(
            select(AgentEvent.payload).where(
                AgentEvent.kind == AgentEventKind.TOOL_CALL
            )
        )
    ).scalar_one()
    assert stored["provider_data"]["provider"] == "gemini"
    assert stored["provider_data"]["response_id"] == "resp-1"
    assert stored["provider_data"]["thought_signature"]
    # And it reads back as part of the call, ready for the next request.
    (tool_call,) = [
        m
        for m in await events.load_messages(db_session, session_id)
        if isinstance(m, ToolCall)
    ]
    assert tool_call.provider_data == stored["provider_data"]
    assert tool_call.call_id == "resp-1:0"


async def test_a_later_turn_replays_earlier_rounds_as_separate_rounds(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Two turns, one call each. On the third request they are still two
    rounds, not one round of two calls: the second was decided after the
    first, and the history says so."""
    google = StandInGoogle(
        answer(add("cookie"), response_id="resp-1"),
        say("One cookie. Anything else?", "resp-2"),
        answer(add("apple_pie"), response_id="resp-3"),
        say("And an apple pie.", "resp-4"),
    )
    model = model_over(google)

    await run_turn(db_session, session_id, "A cookie", model=model, config=CONFIG)
    reply = await run_turn(
        db_session, session_id, "And a pie", model=model, config=CONFIG
    )

    assert shape(google.requests[3]["contents"]) == [
        ("user", ["text"]),
        ("model", ["call:cookie"]),
        ("user", ["result:allow"]),
        ("model", ["text"]),
        ("user", ["text"]),
        ("model", ["call:apple_pie"]),
        ("user", ["result:allow"]),
    ]
    assert reply.cart.item_count == 2


async def test_a_call_sent_twice_by_the_model_still_takes_effect_once(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Gemini gave the call an id, and repeated it. The idempotency key built
    from that id means one cookie, as with any other provider."""
    repeated = types.Part(
        function_call=types.FunctionCall(
            id="fc_1",
            name="add_item",
            args={"item_code": "cookie", "quantity": 1, "selections": []},
        )
    )
    google = StandInGoogle(
        answer(repeated, repeated, response_id="resp-1"), say("One cookie.", "resp-2")
    )

    reply = await run_turn(
        db_session, session_id, "A cookie", model=model_over(google), config=CONFIG
    )

    assert reply.cart.item_count == 1


async def test_the_backend_refusing_a_call_is_sent_back_for_the_model_to_recover(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Fries with no size. The refusal goes back as the function's result, and
    the model's next move is its own."""
    google = StandInGoogle(
        answer(add("fries"), response_id="resp-1"), say("What size fries?", "resp-2")
    )

    reply = await run_turn(
        db_session, session_id, "Some fries", model=model_over(google), config=CONFIG
    )

    assert reply.text == "What size fries?"
    assert reply.cart.lines == []
    assert shape(google.requests[1]["contents"])[-1] == ("user", ["result:reject"])


async def test_every_request_carries_the_instructions_and_all_the_tools(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    google = StandInGoogle(say("Welcome!", "resp-1"))

    await run_turn(
        db_session, session_id, "Hi", model=model_over(google), config=CONFIG
    )

    config = google.requests[0]["config"]
    assert config.system_instruction.startswith("You take orders at the drive-through")
    names = {d.name for d in config.tools[0].function_declarations}
    assert {"add_item", "submit_order", "get_todays_specials", "request_human"} <= names
    assert len(names) == 13
