"""Voice calls, with OpenAI replaced by stand-ins.

The audio itself never touches the backend, so nothing here needs any. What
is under test is the backend's side of a call: what the model asks for runs
through the real tools and policy gateway, what is said is written to the
event log, the clocks follow the conversation, timers are spoken, and the
call ends when the session does.

Events are built from the OpenAI SDK's own types, so a wrong field name fails
here and in the type checker, not on a live call.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from fastapi import FastAPI
from openai.types.realtime import (
    ConversationItemInputAudioTranscriptionCompletedEvent,
    InputAudioBufferSpeechStartedEvent,
    RealtimeErrorEvent,
    ResponseAudioTranscriptDoneEvent,
    ResponseCreatedEvent,
    ResponseDoneEvent,
    ResponseFunctionCallArgumentsDoneEvent,
)
from openai.types.realtime.realtime_error import RealtimeError
from openai.types.realtime.realtime_response import RealtimeResponse
from openai.types.realtime.realtime_response_usage import RealtimeResponseUsage
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events, timekeeper
from drivetruagent.agent.guard import BLOCKED, ENDED, Guard
from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.agent.voice import (
    TIMER_RESPONSE,
    LiveCalls,
    NoVoiceCall,
    VoiceBridge,
    VoiceCallInProgress,
)
from drivetruagent.api.voice import (
    borrowed,
    get_live_calls,
    get_realtime_client,
    get_session_scope,
)
from drivetruagent.config import Settings, get_settings
from drivetruagent.enums import AgentEventKind, Language, SessionState
from drivetruagent.ordering import sessions
from drivetruagent.ordering.cart import add_item, get_cart
from drivetruagent.ordering.sessions import end_session, get_session, open_session
from drivetruagent.ordering.timers import TimerSettings
from drivetruagent.policy.decisions import PolicyLimits
from drivetruagent.policy.guardrails import Verdict

TAX = Decimal("0.13")
TIMERS = TimerSettings()
CONFIG = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=TAX,
    limits=PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
    ),
    max_tool_rounds=8,
)
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
ANSWER = "v=0\r\no=OpenAI 3 4 IN IP4 0.0.0.0\r\ns=-\r\n"

pytestmark = pytest.mark.usefixtures("menu")


class FakeCall:
    """Stands in for the live call. Records what the bridge asked of it."""

    def __init__(self) -> None:
        self.outputs: list[tuple[str, dict[str, Any]]] = []
        self.responses = 0
        self.said: list[str] = []
        self.hung_up = 0

    async def send_function_output(self, call_id: str, output: str) -> None:
        self.outputs.append((call_id, json.loads(output)))

    async def respond(self) -> None:
        self.responses += 1

    async def say(self, sentence: str) -> None:
        self.said.append(sentence)

    async def hang_up(self) -> None:
        self.hung_up += 1


class Pauses:
    """Stands in for asyncio.sleep: records how long it was asked to wait."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


@pytest.fixture
def call() -> FakeCall:
    return FakeCall()


@pytest.fixture
def pauses() -> Pauses:
    return Pauses()


@pytest.fixture
def bridge(
    db_session: AsyncSession, session_id: uuid.UUID, call: FakeCall, pauses: Pauses
) -> VoiceBridge:
    return VoiceBridge(
        session_id=session_id,
        scope=lambda: borrowed(db_session),
        call=call,
        config=CONFIG,
        model_name="gpt-realtime-2.1-mini",
        sleep=pauses,
    )


# --- Events, as OpenAI sends them -------------------------------------------


def speech_started() -> InputAudioBufferSpeechStartedEvent:
    return InputAudioBufferSpeechStartedEvent(
        type="input_audio_buffer.speech_started",
        event_id="e",
        audio_start_ms=0,
        item_id="item_user",
    )


def heard(transcript: str) -> ConversationItemInputAudioTranscriptionCompletedEvent:
    return ConversationItemInputAudioTranscriptionCompletedEvent.model_validate(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "event_id": "e",
            "item_id": "item_user",
            "content_index": 0,
            "transcript": transcript,
            "usage": {"type": "duration", "seconds": 2},
        }
    )


def calls_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    response_id: str = "resp_1",
    call_id: str = "call_1",
) -> ResponseFunctionCallArgumentsDoneEvent:
    return ResponseFunctionCallArgumentsDoneEvent(
        type="response.function_call_arguments.done",
        event_id="e",
        item_id="item_call",
        output_index=0,
        response_id=response_id,
        call_id=call_id,
        name=name,
        arguments=json.dumps(arguments),
    )


def says(
    transcript: str, *, response_id: str = "resp_2"
) -> ResponseAudioTranscriptDoneEvent:
    return ResponseAudioTranscriptDoneEvent(
        type="response.output_audio_transcript.done",
        event_id="e",
        item_id="item_reply",
        output_index=0,
        content_index=0,
        response_id=response_id,
        transcript=transcript,
    )


def response(
    response_id: str,
    *,
    output: list[dict[str, Any]] | None = None,
    metadata: dict[str, str] | None = None,
) -> RealtimeResponse:
    return RealtimeResponse.model_validate(
        {
            "id": response_id,
            "object": "realtime.response",
            "status": "completed",
            "output": output or [],
            "metadata": metadata,
            "usage": RealtimeResponseUsage(
                input_tokens=1500, output_tokens=80, total_tokens=1580
            ).model_dump(),
        }
    )


def created(
    response_id: str, *, metadata: dict[str, str] | None = None
) -> ResponseCreatedEvent:
    return ResponseCreatedEvent(
        type="response.created",
        event_id="e",
        response=response(response_id, metadata=metadata),
    )


def finished(
    response_id: str,
    *,
    output: list[dict[str, Any]] | None = None,
    metadata: dict[str, str] | None = None,
) -> ResponseDoneEvent:
    return ResponseDoneEvent(
        type="response.done",
        event_id="e",
        response=response(response_id, output=output, metadata=metadata),
    )


def a_function_call_item() -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": "item_call",
        "object": "realtime.item",
        "call_id": "call_1",
        "name": "add_item",
        "arguments": "{}",
        "status": "completed",
    }


def a_spoken_item(transcript: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "item_reply",
        "object": "realtime.item",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_audio", "transcript": transcript}],
    }


COOKIE = {"item_code": "cookie", "quantity": 1, "selections": []}


async def logged(db: AsyncSession, session_id: uuid.UUID) -> list[tuple[str, Any]]:
    """Every event of the session as (kind, text, tool name or payload)."""
    out: list[tuple[str, Any]] = []
    for event in await events.load_events(db, session_id):
        payload = event.payload
        out.append(
            (event.kind.value, payload.get("text") or payload.get("name") or payload)
        )
    return out


# --- What the customer says -------------------------------------------------


async def test_what_the_customer_said_is_written_to_the_log(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(speech_started())
    await bridge.handle(heard("  A cookie, please.  "))

    assert await logged(db_session, session_id) == [
        ("user_message", "A cookie, please.")
    ]


async def test_silence_that_was_transcribed_as_nothing_is_not_logged(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(heard("   "))

    assert await logged(db_session, session_id) == []


async def test_the_clocks_stop_while_the_customer_speaks_and_the_agent_answers(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(speech_started())
    assert (await get_session(db_session, session_id)).turn_started_at is not None

    await bridge.handle(says("Hi there!"))
    await bridge.handle(finished("resp_2"))

    customer_session = await get_session(db_session, session_id)
    assert customer_session.turn_started_at is None
    assert customer_session.nudged_at is None


# --- What the model asks for ------------------------------------------------


async def test_a_tool_call_runs_through_the_real_tools_and_its_result_goes_back(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(speech_started())
    await bridge.handle(calls_tool("add_item", COOKIE))

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert [line.item_code for line in cart.lines] == ["cookie"]
    ((call_id, output),) = call.outputs
    assert call_id == "call_1"
    assert output["decision"] == "allow"
    assert output["cart"]["item_count"] == 1


async def test_the_model_is_asked_to_carry_on_once_its_tools_have_answered(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(speech_started())
    await bridge.handle(calls_tool("add_item", COOKIE, response_id="resp_1"))

    await bridge.handle(finished("resp_1", output=[a_function_call_item()]))

    assert call.responses == 1
    # The turn is not over: the agent has not answered yet.
    assert (await get_session(db_session, session_id)).turn_started_at is not None

    await bridge.handle(says("One cookie. Anything else?", response_id="resp_2"))
    await bridge.handle(finished("resp_2", output=[a_spoken_item("One cookie.")]))

    assert call.responses == 1
    assert (await get_session(db_session, session_id)).turn_started_at is None


async def test_a_voice_turn_is_logged_like_a_text_turn(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The live screen and the staff package read the log. They do not need
    to know a conversation was spoken."""
    await bridge.handle(speech_started())
    await bridge.handle(heard("A cookie."))
    await bridge.handle(calls_tool("add_item", COOKIE, response_id="resp_1"))
    await bridge.handle(finished("resp_1", output=[a_function_call_item()]))
    await bridge.handle(says("One cookie. Anything else?", response_id="resp_2"))
    await bridge.handle(finished("resp_2"))

    assert [kind for kind, _ in await logged(db_session, session_id)] == [
        "user_message",
        "tool_call",
        "tool_result",
        "model_call",
        "assistant_message",
        "model_call",
    ]
    turn_ids = {
        event.turn_id for event in await events.load_events(db_session, session_id)
    }
    assert len(turn_ids) == 1


async def test_each_response_is_recorded_with_its_usage(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(finished("resp_9", output=[a_spoken_item("Hi")]))

    (event,) = await events.load_events(db_session, session_id)
    assert event.kind is AgentEventKind.MODEL_CALL
    assert event.payload == {
        "model": "gpt-realtime-2.1-mini",
        "transport": "realtime",
        "status": "completed",
        "input_tokens": 1500,
        "output_tokens": 80,
        "tool_calls": 0,
    }


async def test_the_same_tool_call_delivered_twice_takes_effect_once(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(calls_tool("add_item", COOKIE, response_id="r", call_id="c"))
    await bridge.handle(calls_tool("add_item", COOKIE, response_id="r", call_id="c"))

    assert (await get_cart(db_session, session_id, tax_rate=TAX)).item_count == 1
    assert call.outputs[0] == call.outputs[1]


async def test_the_policy_still_decides_on_a_call(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Fifteen cookies, said aloud, are held for a yes like typed ones."""
    await bridge.handle(calls_tool("add_item", {**COOKIE, "quantity": 15}))

    ((_, output),) = call.outputs
    assert output["decision"] == "confirm"
    assert (await get_cart(db_session, session_id, tax_rate=TAX)).lines == []


async def test_arguments_that_are_not_json_come_back_as_an_invalid_call(
    bridge: VoiceBridge, call: FakeCall
) -> None:
    broken = calls_tool("add_item", {})
    broken.arguments = "{not json"

    await bridge.handle(broken)

    ((_, output),) = call.outputs
    assert output["error"]["code"] == "invalid_tool_call"


# --- What the agent says ----------------------------------------------------


async def test_what_the_agent_said_is_written_to_the_log(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.handle(says("Welcome to Maple Lane Burgers!"))

    assert await logged(db_session, session_id) == [
        ("assistant_message", "Welcome to Maple Lane Burgers!")
    ]


async def test_a_timer_sentence_is_not_logged_twice(
    bridge: VoiceBridge, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The timekeeper already wrote "Are you still there?" to the log. When the
    model says it, that is not a second thing the agent said."""
    timer = {"purpose": TIMER_RESPONSE}
    await bridge.handle(created("resp_t", metadata=timer))
    await bridge.handle(says("Are you still there?", response_id="resp_t"))
    await bridge.handle(finished("resp_t", metadata=timer))

    assert await logged(db_session, session_id) == []


# --- The end of a call ------------------------------------------------------


async def test_the_call_ends_after_the_order_is_placed_and_the_goodbye_said(
    bridge: VoiceBridge,
    call: FakeCall,
    pauses: Pauses,
    db_session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)
    await bridge.handle(
        calls_tool("request_confirmation", {}, response_id="r1", call_id="c1")
    )
    await bridge.handle(finished("r1", output=[a_function_call_item()]))
    await bridge.handle(calls_tool("submit_order", {}, response_id="r2", call_id="c2"))
    await bridge.handle(finished("r2", output=[a_function_call_item()]))
    goodbye = "Thanks! Your order number is 1. Please pull forward."

    await bridge.handle(finished("r3", output=[a_spoken_item(goodbye)]))

    assert (await get_session(db_session, session_id)).state is SessionState.SUBMITTED
    assert call.hung_up == 1
    assert bridge.ended
    # It waited for the goodbye to be said before hanging up.
    assert pauses.seconds and pauses.seconds[-1] >= 3


async def test_the_call_carries_on_while_the_session_is_in_progress(
    bridge: VoiceBridge, call: FakeCall
) -> None:
    await bridge.handle(finished("r1", output=[a_spoken_item("Anything else?")]))

    assert call.hung_up == 0
    assert not bridge.ended


async def test_an_error_from_openai_is_logged_and_the_call_carries_on(
    bridge: VoiceBridge, call: FakeCall, caplog: pytest.LogCaptureFixture
) -> None:
    error = RealtimeErrorEvent(
        type="error",
        event_id="e",
        error=RealtimeError(type="invalid_request_error", message="Unknown item."),
    )

    await bridge.handle(error)

    assert "Unknown item." in caplog.text
    assert call.hung_up == 0


# --- Timers on a call -------------------------------------------------------


async def nudge(db: AsyncSession, session_id: uuid.UUID, seconds: int) -> None:
    started = (await get_session(db, session_id)).started_at
    await timekeeper.enforce(
        db,
        session_id,
        now=started + timedelta(seconds=seconds),
        timers=TIMERS,
        tax_rate=TAX,
    )
    await db.commit()


async def test_a_nudge_is_spoken_on_the_call(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.start_watching_timers()

    await nudge(db_session, session_id, 20)
    await bridge.check_timers()

    assert call.said == ["Are you still there?"]
    assert call.hung_up == 0


async def test_each_timer_is_spoken_once(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.start_watching_timers()
    await nudge(db_session, session_id, 20)

    for _ in range(3):
        await bridge.check_timers()

    assert call.said == ["Are you still there?"]


async def test_timers_from_before_the_call_are_not_spoken(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await nudge(db_session, session_id, 20)

    await bridge.start_watching_timers()
    await bridge.check_timers()

    assert call.said == []


async def test_a_session_closed_for_silence_hangs_up_the_call(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.start_watching_timers()
    await nudge(db_session, session_id, 20)
    await nudge(db_session, session_id, 40)

    await bridge.check_timers()

    assert call.said == ["Are you still there?"]
    assert call.hung_up == 1


async def test_the_handoff_is_said_and_then_the_call_ends(
    bridge: VoiceBridge,
    call: FakeCall,
    pauses: Pauses,
    db_session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    await bridge.start_watching_timers()
    started = (await get_session(db_session, session_id)).started_at
    three_minutes = started + timedelta(seconds=180)
    await sessions.end_turn(db_session, session_id, now=three_minutes)
    await timekeeper.enforce(
        db_session, session_id, now=three_minutes, timers=TIMERS, tax_rate=TAX
    )
    await db_session.commit()

    await bridge.check_timers()

    assert call.said == [
        "A team member will help you finish your order. One moment, please."
    ]
    assert pauses.seconds and call.hung_up == 1


async def test_hanging_up_twice_is_hanging_up_once(
    bridge: VoiceBridge, call: FakeCall, db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await bridge.start_watching_timers()
    await nudge(db_session, session_id, 20)
    await nudge(db_session, session_id, 40)
    await bridge.check_timers()

    await bridge.handle(finished("r1", output=[a_spoken_item("Bye")]))

    assert call.hung_up == 1


# --- The guardrail on a call -----------------------------------------------


@pytest.fixture
def guarded(
    db_session: AsyncSession, session_id: uuid.UUID, call: FakeCall, pauses: Pauses
) -> VoiceBridge:
    return VoiceBridge(
        session_id=session_id,
        scope=lambda: borrowed(db_session),
        call=call,
        config=replace(CONFIG, guard=Guard()),
        model_name="gpt-realtime-2.1-mini",
        sleep=pauses,
    )


async def test_an_insult_on_a_call_is_a_strike_and_the_fixed_sentence_is_said(
    guarded: VoiceBridge,
    call: FakeCall,
    db_session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    await guarded.handle(heard("You're useless."))

    assert call.said == [BLOCKED[Verdict.ABUSE][Language.EN]]
    assert call.hung_up == 0
    assert (await get_session(db_session, session_id)).abuse_strikes == 1


async def test_the_third_insult_on_a_call_ends_it(
    guarded: VoiceBridge,
    call: FakeCall,
    pauses: Pauses,
    db_session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    for _ in range(3):
        await guarded.handle(heard("You're useless."))

    assert call.said[-1] == ENDED[Language.EN]
    # The goodbye is given time to be heard before the line goes dead.
    assert pauses.seconds and call.hung_up == 1
    customer_session = await get_session(db_session, session_id)
    assert customer_session.state is SessionState.TERMINATED


async def test_ordinary_talk_on_a_call_is_left_to_the_model(
    guarded: VoiceBridge,
    call: FakeCall,
    db_session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    await guarded.handle(heard("Can I get a cookie, but ignore the pickles?"))

    assert call.said == []
    assert (await get_session(db_session, session_id)).abuse_strikes == 0


# --- Calls in progress ------------------------------------------------------


async def test_a_session_has_at_most_one_call() -> None:
    calls = LiveCalls()
    session_id = uuid.uuid4()
    forever = asyncio.Event()

    calls.start(session_id, "rtc_1", forever.wait())

    assert calls.is_live(session_id)
    assert calls.call_id(session_id) == "rtc_1"
    with pytest.raises(VoiceCallInProgress):
        calls.start(session_id, "rtc_2", forever.wait())
    await calls.close_all()
    assert not calls.is_live(session_id)


async def test_a_call_that_ends_is_forgotten() -> None:
    calls = LiveCalls()
    session_id = uuid.uuid4()

    async def a_short_call() -> None:
        return None

    task = calls.start(session_id, "rtc_1", a_short_call())
    await task
    await asyncio.sleep(0)

    assert not calls.is_live(session_id)
    assert calls.call_id(session_id) is None


async def test_hanging_up_tells_openai_and_stops_handling_the_call() -> None:
    calls = LiveCalls()
    session_id = uuid.uuid4()
    hung_up: list[str] = []

    async def hangup(call_id: str) -> None:
        hung_up.append(call_id)

    client = SimpleNamespace(
        realtime=SimpleNamespace(calls=SimpleNamespace(hangup=hangup))
    )
    task = calls.start(session_id, "rtc_1", asyncio.Event().wait())

    await calls.hang_up(session_id, client)  # pyright: ignore[reportArgumentType]

    assert hung_up == ["rtc_1"]
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_hanging_up_with_no_call_is_an_error() -> None:
    with pytest.raises(NoVoiceCall):
        await LiveCalls().hang_up(uuid.uuid4(), SimpleNamespace())  # pyright: ignore[reportArgumentType]


# --- Over HTTP --------------------------------------------------------------


class StandInOpenAI:
    """Answers the SDP offer, and lets the backend join a call that either
    ends at once or stays open until it is cancelled."""

    def __init__(self, *, call_stays_open: bool = False, refuse: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self.hung_up: list[str] = []
        self.joined: list[str] = []
        self._stays_open = call_stays_open
        self._refuse = refuse
        raw = SimpleNamespace(create=self._create)
        self.realtime = SimpleNamespace(
            calls=SimpleNamespace(with_raw_response=raw, hangup=self._hangup),
            connect=self._connect,
        )

    async def _create(self, **request: Any) -> Any:
        if self._refuse:
            raise openai.OpenAIError("Connection refused.")
        self.created.append(request)
        return SimpleNamespace(
            headers={"location": "/v1/realtime/calls/rtc_abc123"}, text=ANSWER
        )

    async def _hangup(self, call_id: str) -> None:
        self.hung_up.append(call_id)

    def _connect(self, *, call_id: str) -> Any:
        self.joined.append(call_id)
        stays_open = self._stays_open

        @asynccontextmanager
        async def joined() -> AsyncIterator[Any]:
            class Connection:
                def __aiter__(self) -> "Connection":
                    return self

                async def __anext__(self) -> Any:
                    if stays_open:
                        await asyncio.Event().wait()
                    raise StopAsyncIteration

            yield Connection()

        return joined()


@pytest.fixture
def openai_stand_in(app: FastAPI, db_session: AsyncSession) -> StandInOpenAI:
    stand_in = StandInOpenAI()
    calls = LiveCalls()
    app.dependency_overrides[get_realtime_client] = lambda: stand_in
    app.dependency_overrides[get_live_calls] = lambda: calls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)
    return stand_in


async def start_call(
    client: httpx.AsyncClient, session_id: str, offer: str = OFFER
) -> httpx.Response:
    return await client.post(
        f"/sessions/{session_id}/voice",
        content=offer,
        headers={"Content-Type": "application/sdp"},
    )


@pytest.fixture
async def http_session(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


async def test_the_browsers_offer_is_answered_with_openais_answer(
    client: httpx.AsyncClient, http_session: str, openai_stand_in: StandInOpenAI
) -> None:
    response = await start_call(client, http_session)

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/sdp")
    assert response.text == ANSWER
    (request,) = openai_stand_in.created
    assert request["sdp"] == OFFER


async def test_the_call_is_set_up_like_the_text_agent(
    client: httpx.AsyncClient, http_session: str, openai_stand_in: StandInOpenAI
) -> None:
    await start_call(client, http_session)

    session = openai_stand_in.created[0]["session"]
    assert session["type"] == "realtime"
    assert session["model"] == "gpt-realtime-2.1-mini"
    assert session["instructions"].startswith("You take orders at the drive-through")
    assert len(session["tools"]) == len(TOOL_DEFINITIONS)
    assert {"type", "name", "description", "parameters"} == set(session["tools"][0])
    assert session["audio"]["input"]["transcription"] == {
        "model": "gpt-4o-mini-transcribe"
    }
    assert session["audio"]["output"] == {"voice": "marin"}


async def test_the_backend_joins_the_call_it_answered(
    client: httpx.AsyncClient, http_session: str, openai_stand_in: StandInOpenAI
) -> None:
    await start_call(client, http_session)
    await asyncio.sleep(0.05)

    assert openai_stand_in.joined == ["rtc_abc123"]


async def test_a_second_call_on_the_same_session_is_refused(
    app: FastAPI, client: httpx.AsyncClient, http_session: str, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    calls = LiveCalls()
    app.dependency_overrides[get_realtime_client] = lambda: stand_in
    app.dependency_overrides[get_live_calls] = lambda: calls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)

    first = await start_call(client, http_session)
    second = await start_call(client, http_session)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "voice_call_in_progress"
    await calls.close_all()


async def test_hanging_up_over_http(
    app: FastAPI, client: httpx.AsyncClient, http_session: str, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    calls = LiveCalls()
    app.dependency_overrides[get_realtime_client] = lambda: stand_in
    app.dependency_overrides[get_live_calls] = lambda: calls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)
    await start_call(client, http_session)

    response = await client.delete(f"/sessions/{http_session}/voice")
    await asyncio.sleep(0.01)

    assert response.status_code == 204
    assert stand_in.hung_up == ["rtc_abc123"]
    assert not calls.is_live(uuid.UUID(http_session))


async def test_hanging_up_when_there_is_no_call_is_not_found(
    client: httpx.AsyncClient, http_session: str, openai_stand_in: StandInOpenAI
) -> None:
    response = await client.delete(f"/sessions/{http_session}/voice")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_voice_call"


async def test_a_call_cannot_start_on_a_session_that_has_ended(
    client: httpx.AsyncClient, db_session: AsyncSession, openai_stand_in: StandInOpenAI
) -> None:
    session_id = (await open_session(db_session, "lane-9")).id
    await end_session(db_session, session_id, SessionState.ABANDONED, "test")
    await db_session.commit()

    response = await start_call(client, str(session_id))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_ended"
    assert openai_stand_in.created == []


async def test_a_call_on_an_unknown_session_is_not_found(
    client: httpx.AsyncClient, openai_stand_in: StandInOpenAI
) -> None:
    response = await start_call(client, str(uuid.uuid4()))

    assert response.status_code == 404


@pytest.mark.parametrize("body", ["", "hello", '{"sdp": "v=0"}'])
async def test_a_body_that_is_not_an_offer_is_refused(
    client: httpx.AsyncClient,
    http_session: str,
    openai_stand_in: StandInOpenAI,
    body: str,
) -> None:
    response = await start_call(client, http_session, body)

    assert response.status_code == 422
    assert openai_stand_in.created == []


async def test_without_an_openai_key_voice_is_unavailable(
    client: httpx.AsyncClient, http_session: str
) -> None:
    """The test app's settings have no key, and nothing is stood in."""
    response = await start_call(client, http_session)

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["error"]["message"]


async def test_openai_refusing_the_call_is_reported(
    app: FastAPI, client: httpx.AsyncClient, http_session: str, db_session: AsyncSession
) -> None:
    app.dependency_overrides[get_realtime_client] = lambda: StandInOpenAI(refuse=True)
    app.dependency_overrides[get_live_calls] = LiveCalls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)

    response = await start_call(client, http_session)

    assert response.status_code == 503
    assert response.json()["error"]["message"].startswith(
        "OpenAI did not accept the call"
    )


async def test_agent_info_says_whether_voice_can_be_used(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    without = (await client.get("/agent")).json()
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        openai_api_key="test-key",  # pyright: ignore[reportArgumentType]
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with_key = (await client.get("/agent")).json()

    assert (without["voice_available"], without["voice_model"]) == (False, None)
    assert (with_key["voice_available"], with_key["voice_model"]) == (
        True,
        "gpt-realtime-2.1-mini",
    )
