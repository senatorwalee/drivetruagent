"""Voice: the customer talks to an OpenAI Realtime model over WebRTC.

    browser  --audio (WebRTC)-->  OpenAI Realtime  <--events (WebSocket)--  this backend

The audio goes straight between the browser and OpenAI, which keeps it quick
and lets the browser handle echo and noise. Everything else stays here: the
API key, the instructions, and every tool call. The backend joins the call
on a second connection (OpenAI calls it a "sideband"), sees what the model
asks for, runs it through the same tools and policy gateway as text mode,
and sends the result back. The browser never sees a key and cannot answer a
tool call itself.

What is said, both ways, is written to the event log as text, from OpenAI's
transcripts. The log stays the one record of a conversation, so the live
screen, the staff handoff package and the timers work as they do for text.

`VoiceBridge` holds the rules and is tested with a stand-in connection.
`run_call` is the thin part that connects it to OpenAI.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI
from openai.types.realtime import (
    RealtimeServerEvent,
    RealtimeSessionCreateRequestParam,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.guard import check_message
from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.messages import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelUnavailable, ToolDefinition
from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.db.models import AgentEvent
from drivetruagent.enums import FINAL_SESSION_STATES, AgentEventKind
from drivetruagent.ordering.errors import OrderingError
from drivetruagent.ordering.offers import close_suggestions
from drivetruagent.ordering.sessions import begin_turn, end_turn, get_session
from drivetruagent.policy.guardrails import GuardAction

logger = logging.getLogger(__name__)

# Marks a response the backend asked for itself (a timer speaking), so its
# words are not written to the log a second time.
TIMER_RESPONSE = "timer"
# How often the call looks for something a timer has done.
TIMER_POLL_SECONDS = 1.0

type SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class VoiceSettings:
    model: str = "gpt-realtime-2.1-mini"
    voice: str = "marin"
    # Writes down what the customer said. The model answers the audio, not
    # this text; the text is the record.
    transcription_model: str = "gpt-4o-mini-transcribe"


def session_config(
    instructions: str, tools: Sequence[ToolDefinition], settings: VoiceSettings
) -> RealtimeSessionCreateRequestParam:
    """How the call is set up: the same instructions and tools as text mode."""
    return {
        "type": "realtime",
        "model": settings.model,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {"model": settings.transcription_model},
                # Waits for the customer to finish a thought, not just a
                # pause for breath, before answering.
                "turn_detection": {"type": "semantic_vad"},
            },
            "output": {"voice": settings.voice},
        },
        "tools": [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ],
        "tool_choice": "auto",
    }


class CallControl(Protocol):
    """What the bridge can do to the call. The real one talks to OpenAI."""

    async def send_function_output(self, call_id: str, output: str) -> None: ...

    async def respond(self) -> None:
        """Ask the model to carry on, now that tool results are in."""
        ...

    async def say(self, sentence: str) -> None:
        """Have the model say a fixed sentence, outside the conversation."""
        ...

    async def hang_up(self) -> None: ...


def speaking_seconds(text: str) -> float:
    """Roughly how long a sentence takes to say, so a goodbye is not cut off."""
    return min(12.0, 1.5 + len(text) * 0.06)


class VoiceBridge:
    """Turns what happens on the call into the same effects as text mode.

    One instance per call. Events arrive one at a time from the call; each
    is handled in its own short database session, committed as it goes, as
    the text loop does, so the screen sees each change as it happens.
    """

    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        scope: SessionScope,
        call: CallControl,
        config: AgentConfig,
        model_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.session_id = session_id
        self._scope = scope
        self._call = call
        self._config = config
        self._model_name = model_name
        self._clock = clock
        self._sleep = sleep
        self._turn_id = uuid.uuid4()
        # Responses that asked for tools: once they finish, the model is
        # asked to carry on with the results.
        self._awaiting_results: set[str] = set()
        # Responses the backend asked for to say a timer's sentence.
        self._timer_responses: set[str] = set()
        self._last_timer_seq = 0
        self.ended = False

    # --- Events from the call -------------------------------------------

    async def handle(self, event: RealtimeServerEvent) -> None:
        if event.type == "input_audio_buffer.speech_started":
            await self._customer_started_speaking()
        elif event.type == "conversation.item.input_audio_transcription.completed":
            await self._customer_said(event.transcript)
        elif event.type == "response.created":
            metadata = event.response.metadata or {}
            if metadata.get("purpose") == TIMER_RESPONSE and event.response.id:
                self._timer_responses.add(event.response.id)
        elif event.type == "response.function_call_arguments.done":
            await self._model_called(
                event.response_id, event.call_id, event.name, event.arguments
            )
        elif event.type == "response.output_audio_transcript.done":
            if not self._is_timer_response(event.response_id):
                await self._agent_said(event.transcript)
        elif event.type == "response.done":
            await self._response_finished(event.response)
        elif event.type == "error":
            logger.warning(
                "Realtime error on session %s: %s", self.session_id, event.error.message
            )

    async def _customer_started_speaking(self) -> None:
        # A new turn. The clocks stop until the agent has answered.
        self._turn_id = uuid.uuid4()
        async with self._scope() as db:
            await begin_turn(db, self.session_id, now=self._clock())
            await db.commit()

    async def _customer_said(self, transcript: str) -> None:
        text = transcript.strip()
        if not text:
            return
        async with self._scope() as db:
            await events.record_message(
                db, self.session_id, self._turn_id, UserMessage(text)
            )
            # As in text mode: anything offered last turn has had its chance.
            await close_suggestions(db, self.session_id)
            await db.commit()
        await self._check(text)

    async def _check(self, text: str) -> None:
        """The guardrail, on a call. The model heard the audio and has likely
        answered already, so a blocked message cannot be kept from it here.
        Strikes still count, the fixed sentence is said, and at three the
        call ends."""
        guard = self._config.guard
        if guard is None:
            return
        async with self._scope() as db:
            outcome = await check_message(
                db,
                self.session_id,
                self._turn_id,
                text,
                guard=guard,
                tax_rate=self._config.tax_rate,
            )
            await db.commit()
        if outcome.reply is None:
            return
        await self._call.say(outcome.reply)
        if outcome.action in (GuardAction.TERMINATE, GuardAction.HAND_OFF):
            await self._sleep(speaking_seconds(outcome.reply))
            await self._end_call()

    async def _model_called(
        self, response_id: str, call_id: str, name: str, raw_arguments: str
    ) -> None:
        tool_call = ToolCall(call_id, name, _parse_arguments(raw_arguments))
        async with self._scope() as db:
            context = ToolContext(
                db,
                self.session_id,
                self._config.tax_rate,
                self._config.limits,
                self._config.reservation_ttl,
                # Keyed by the response and the call, as text mode keys by
                # the turn and the call: run twice, it takes effect once.
                idempotency_key=f"{response_id}:{call_id}",
                specials_cache=self._config.specials_cache,
                knowledge=self._config.knowledge,
            )
            result = await run_tool(context, name, tool_call.arguments)
            await events.record_message(db, self.session_id, self._turn_id, tool_call)
            await events.record_message(
                db, self.session_id, self._turn_id, ToolResult(call_id, name, result)
            )
            await db.commit()
        self._awaiting_results.add(response_id)
        await self._call.send_function_output(
            call_id, json.dumps(result, ensure_ascii=False)
        )

    async def _agent_said(self, transcript: str) -> None:
        text = transcript.strip()
        if not text:
            return
        async with self._scope() as db:
            await events.record_message(
                db, self.session_id, self._turn_id, AssistantMessage(text)
            )
            await db.commit()

    async def _response_finished(self, response: Any) -> None:
        response_id = response.id or ""
        if self._is_timer_response(response_id):
            return
        await self._record_model_call(response)
        if response_id in self._awaiting_results:
            # The model asked for tools and has their results. Let it answer.
            self._awaiting_results.discard(response_id)
            await self._call.respond()
            return

        async with self._scope() as db:
            await end_turn(db, self.session_id, now=self._clock())
            await db.commit()
            ended = (
                await get_session(db, self.session_id)
            ).state in FINAL_SESSION_STATES
        if ended:
            # The order was placed or a person is taking over. Let the agent
            # finish its last sentence, then end the call.
            await self._sleep(speaking_seconds(_spoken_text(response)))
            await self._end_call()

    async def _record_model_call(self, response: Any) -> None:
        usage = response.usage
        async with self._scope() as db:
            await events.record(
                db,
                self.session_id,
                self._turn_id,
                AgentEventKind.MODEL_CALL,
                {
                    "model": self._model_name,
                    "transport": "realtime",
                    "status": response.status,
                    "input_tokens": usage.input_tokens if usage else None,
                    "output_tokens": usage.output_tokens if usage else None,
                    "tool_calls": sum(
                        1
                        for item in response.output or []
                        if getattr(item, "type", None) == "function_call"
                    ),
                },
            )
            await db.commit()

    def _is_timer_response(self, response_id: str) -> bool:
        return response_id in self._timer_responses

    # --- What the timers did --------------------------------------------

    async def start_watching_timers(self) -> None:
        """Remember where the log is now, so only new timer events count."""
        async with self._scope() as db:
            latest = await db.execute(
                select(func.coalesce(func.max(AgentEvent.seq), 0)).where(
                    AgentEvent.session_id == self.session_id
                )
            )
            self._last_timer_seq = int(latest.scalar_one())

    async def check_timers(self) -> None:
        """Speak or act on anything a timer has done since the last check.

        The timekeeper decides, as it does for text sessions, and writes what
        the customer should hear to the log. On a call nothing is heard until
        the model says it, so the sentence is passed on to be said.
        """
        async with self._scope() as db:
            rows = (
                await db.execute(
                    select(AgentEvent)
                    .where(
                        AgentEvent.session_id == self.session_id,
                        AgentEvent.seq > self._last_timer_seq,
                    )
                    .order_by(AgentEvent.seq)
                )
            ).scalars()
            new_events = list(rows)
        if not new_events:
            return
        self._last_timer_seq = new_events[-1].seq

        for index, event in enumerate(new_events):
            if event.kind is not AgentEventKind.TIMER:
                continue
            action = event.payload.get("action")
            sentence = _sentence_after(new_events, index)
            if action == "abandon":
                await self._end_call()
            elif sentence:
                await self._call.say(sentence)
                if action == "hand_off":
                    await self._sleep(speaking_seconds(sentence))
                    await self._end_call()

    async def _end_call(self) -> None:
        if not self.ended:
            self.ended = True
            await self._call.hang_up()


def _sentence_after(logged: list[AgentEvent], index: int) -> str | None:
    """The fixed sentence a timer recorded right after its own event."""
    following = logged[index + 1] if index + 1 < len(logged) else None
    if following is not None and following.kind is AgentEventKind.ASSISTANT_MESSAGE:
        return str(following.payload.get("text", ""))
    return None


def _spoken_text(response: Any) -> str:
    """The words of a finished response, for timing how long it takes to say."""
    spoken: list[str] = []
    for item in response.output or []:
        for part in getattr(item, "content", None) or []:
            transcript = getattr(part, "transcript", None)
            if transcript:
                spoken.append(transcript)
    return " ".join(spoken)


def _parse_arguments(raw: str) -> dict[str, Any]:
    """The model's arguments. Anything that is not a JSON object is passed on
    under a key the tools reject, so the model is told and can try again."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparseable_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_unparseable_arguments": raw}


# --- The real connection ----------------------------------------------------

# OpenAI's client, as the rest of the app refers to it. Only this module (and
# the text adapter) imports OpenAI's library.
type RealtimeClient = AsyncOpenAI


def realtime_client(api_key: str) -> RealtimeClient:
    return AsyncOpenAI(api_key=api_key)


async def create_call(
    client: RealtimeClient, offer: str, config: RealtimeSessionCreateRequestParam
) -> tuple[str, str]:
    """Hand the browser's offer to OpenAI. Returns (call id, answer SDP).

    Raises ModelUnavailable if OpenAI does not accept it.
    """
    try:
        created = await client.realtime.calls.with_raw_response.create(
            sdp=offer, session=config
        )
    except openai.OpenAIError as error:
        raise ModelUnavailable(f"OpenAI did not accept the call: {error}") from error
    # OpenAI names the call in the Location header; the id is its last part.
    call_id = created.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
    return call_id, created.text


class OpenAICall:
    """CallControl over the sideband connection to a live call."""

    def __init__(self, client: AsyncOpenAI, call_id: str, connection: Any) -> None:
        self._client = client
        self._call_id = call_id
        self._connection = connection

    async def send_function_output(self, call_id: str, output: str) -> None:
        await self._connection.conversation.item.create(
            item={"type": "function_call_output", "call_id": call_id, "output": output}
        )

    async def respond(self) -> None:
        await self._connection.response.create()

    async def say(self, sentence: str) -> None:
        await self._connection.response.create(
            response={
                # Said once, and not added to the conversation the model sees.
                "conversation": "none",
                "metadata": {"purpose": TIMER_RESPONSE},
                "instructions": (
                    "Say exactly this, in the same language, and nothing else: "
                    f"{sentence}"
                ),
            }
        )

    async def hang_up(self) -> None:
        await self._client.realtime.calls.hangup(self._call_id)


async def run_call(
    client: AsyncOpenAI,
    call_id: str,
    bridge_for: Callable[[CallControl], VoiceBridge],
) -> None:
    """Join a live call and handle it until it ends.

    Two things run side by side: reading the call's events, and checking
    once a second for anything a timer has done.
    """
    async with client.realtime.connect(call_id=call_id) as connection:
        bridge = bridge_for(OpenAICall(client, call_id, connection))
        await bridge.start_watching_timers()
        watching = asyncio.create_task(_watch_timers(bridge))
        try:
            async for event in _events(connection):
                await bridge.handle(event)
                if bridge.ended:
                    break
        finally:
            watching.cancel()


async def _events(connection: Any) -> AsyncIterator[RealtimeServerEvent]:
    async for event in connection:
        yield event


async def _watch_timers(bridge: VoiceBridge) -> None:
    while not bridge.ended:
        try:
            await bridge.check_timers()
        except Exception:
            logger.exception("Checking timers failed on session %s", bridge.session_id)
        await asyncio.sleep(TIMER_POLL_SECONDS)


# --- Calls in progress --------------------------------------------------------


class VoiceCallInProgress(OrderingError):
    code = "voice_call_in_progress"

    def __init__(self) -> None:
        super().__init__("This session already has a voice call. Hang it up first.")


class NoVoiceCall(OrderingError):
    code = "no_voice_call"

    def __init__(self) -> None:
        super().__init__("This session has no voice call.")


@dataclass
class LiveCall:
    call_id: str
    task: asyncio.Task[object]


class LiveCalls:
    """The calls this process is handling, one per session at most.

    A call lives in the process that answered it: its sideband connection is
    held here. That is fine for one server; with several, a call would need
    to be routed back to the process holding it.
    """

    def __init__(self) -> None:
        self._calls: dict[uuid.UUID, LiveCall] = {}

    def is_live(self, session_id: uuid.UUID) -> bool:
        call = self._calls.get(session_id)
        return call is not None and not call.task.done()

    def call_id(self, session_id: uuid.UUID) -> str | None:
        call = self._calls.get(session_id)
        return call.call_id if call else None

    def start(
        self,
        session_id: uuid.UUID,
        call_id: str,
        handling: Coroutine[Any, Any, object],
    ) -> asyncio.Task[object]:
        """Handle a call in the background. It is forgotten when it ends."""
        if self.is_live(session_id):
            # Never started, so close it: otherwise Python warns it was
            # never awaited.
            handling.close()
            raise VoiceCallInProgress()
        task: asyncio.Task[object] = asyncio.create_task(handling)
        self._calls[session_id] = LiveCall(call_id, task)

        def forget(finished: asyncio.Task[object]) -> None:
            if self._calls.get(session_id) is not None and (
                self._calls[session_id].task is finished
            ):
                del self._calls[session_id]
            if not finished.cancelled() and finished.exception() is not None:
                logger.error(
                    "Voice call on session %s failed",
                    session_id,
                    exc_info=finished.exception(),
                )

        task.add_done_callback(forget)
        return task

    async def hang_up(self, session_id: uuid.UUID, client: AsyncOpenAI) -> None:
        """End a session's call. Raises NoVoiceCall if there is none."""
        call = self._calls.get(session_id)
        if call is None or call.task.done():
            raise NoVoiceCall()
        try:
            await client.realtime.calls.hangup(call.call_id)
        finally:
            call.task.cancel()

    async def close_all(self) -> None:
        """At shutdown: stop handling every call."""
        tasks = [call.task for call in self._calls.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._calls.clear()
