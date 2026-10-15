"""One customer turn: text in, text out, with tool calls in between.

    record what the customer said
    repeat, up to a limit:
        ask the model for its next step
        if it asked for tools: run them, record call and result, go round again
        if it answered in words: record the reply and stop

The loop knows nothing about any model provider; it talks to a ModelClient.

Unlike the ordering functions, the loop commits. Each tool's work is committed
as soon as it is done, for two reasons: the lock that tool took on the session
is released before the next model call, which can take seconds, and the order
screen sees each change as it happens.
"""

import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events, timekeeper, tracing
from drivetruagent.agent.guard import Guard, check_message, turn_went_nowhere
from drivetruagent.agent.messages import AssistantMessage, ToolResult, UserMessage
from drivetruagent.agent.model import ModelClient, ModelTurn
from drivetruagent.agent.prompt import build_instructions
from drivetruagent.agent.tools import TOOL_DEFINITIONS, ToolContext, run_tool
from drivetruagent.enums import AgentEventKind, SessionState
from drivetruagent.knowledge.store import KnowledgeSearch
from drivetruagent.ordering.cart import get_cart
from drivetruagent.ordering.errors import SessionEnded
from drivetruagent.ordering.inventory import DEFAULT_RESERVATION_TTL
from drivetruagent.ordering.offers import SpecialsCache, close_suggestions
from drivetruagent.ordering.sessions import begin_turn, end_turn, get_session
from drivetruagent.ordering.timers import TimerSettings
from drivetruagent.ordering.views import CartSnapshot
from drivetruagent.policy.decisions import PolicyLimits

# Said when the model keeps asking for tools and never answers. It is a fixed
# sentence so the customer always hears something sensible.
GAVE_UP_REPLY = "Sorry, I'm having trouble with that. Could you say it again?"


@dataclass(frozen=True)
class AgentConfig:
    restaurant_name: str
    tax_rate: Decimal
    limits: PolicyLimits
    max_tool_rounds: int
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL
    # Shared between sessions by whoever runs the loop. None: no caching.
    specials_cache: SpecialsCache | None = None
    # The session length limit, checked as each turn starts. None: no limit.
    timers: TimerSettings | None = None
    # Checks each customer message for misuse. None: no checks.
    guard: Guard | None = None
    # Searches the approved knowledge documents. None: search finds nothing.
    knowledge: KnowledgeSearch | None = None


@dataclass(frozen=True)
class AgentReply:
    text: str
    cart: CartSnapshot
    turn_id: uuid.UUID
    # Names of the tools used this turn, in order. Useful for tests and evals.
    tools_used: tuple[str, ...]
    # True if the round limit was hit and the fixed fallback was said.
    gave_up: bool = False


async def run_turn(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_text: str,
    *,
    model: ModelClient,
    config: AgentConfig,
) -> AgentReply:
    """Handle one thing the customer said and return what to say back.

    Raises SessionNotFound, or SessionEnded if the session was ended for
    abuse: that is not undone by talking to it. A customer who has just
    ordered can still say thanks.
    """
    customer_session = await get_session(db, session_id)
    if customer_session.state is SessionState.TERMINATED:
        raise SessionEnded(customer_session.state)
    turn_id = uuid.uuid4()
    await events.record_message(db, session_id, turn_id, UserMessage(user_text))
    # The customer has answered, one way or another, anything they were
    # offered last turn. It is not offered again.
    await close_suggestions(db, session_id)
    # From here until the reply, the session's clocks stand still: the agent
    # taking its time is not the customer being silent.
    await begin_turn(db, session_id, now=datetime.now(UTC))
    await db.commit()

    try:
        with tracing.span(
            "turn",
            OpenInferenceSpanKindValues.AGENT,
            session_id=session_id,
            input_value=user_text,
            attributes=tracing.metadata({"turn_id": str(turn_id)}),
        ) as turn_span:
            reply = await _answer(db, session_id, turn_id, user_text, model, config)
            tracing.set_output(turn_span, reply.text)
            turn_span.set_attributes(
                tracing.metadata(
                    {
                        "turn_id": str(turn_id),
                        "tools_used": list(reply.tools_used),
                        "gave_up": reply.gave_up,
                        "state": reply.cart.state.value,
                    }
                )
            )
            return reply
    finally:
        # Whatever happened, the turn is over. Anything left unsaved by a
        # failure is dropped first, so this always has a usable transaction.
        await db.rollback()
        await end_turn(db, session_id, now=datetime.now(UTC))
        await db.commit()


async def _answer(
    db: AsyncSession,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    user_text: str,
    model: ModelClient,
    config: AgentConfig,
) -> AgentReply:
    """The turn itself: the time limit, the guardrail, then the model."""
    handed_off = await _hand_off_if_over_time(db, session_id, config)
    if handed_off is not None:
        return await _fixed_reply(db, session_id, turn_id, handed_off, config)
    if config.guard is not None:
        checked = await check_message(
            db,
            session_id,
            turn_id,
            user_text,
            guard=config.guard,
            tax_rate=config.tax_rate,
        )
        await db.commit()
        if checked.reply is not None:
            # Blocked, or the session ended: the model is not asked.
            return await _fixed_reply(db, session_id, turn_id, checked.reply, config)

    reply = await _converse(db, session_id, turn_id, model=model, config=config)
    if reply.gave_up and config.guard is not None:
        # Getting nowhere counts toward a person taking over.
        stuck = await turn_went_nowhere(
            db, session_id, turn_id, guard=config.guard, tax_rate=config.tax_rate
        )
        await db.commit()
        if stuck.reply is not None:
            return await _fixed_reply(db, session_id, turn_id, stuck.reply, config)
    return reply


async def _fixed_reply(
    db: AsyncSession,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    text: str,
    config: AgentConfig,
) -> AgentReply:
    """A reply the backend chose, already in the log, with no model involved."""
    return AgentReply(
        text=text,
        cart=await get_cart(db, session_id, tax_rate=config.tax_rate),
        turn_id=turn_id,
        tools_used=(),
    )


async def _hand_off_if_over_time(
    db: AsyncSession, session_id: uuid.UUID, config: AgentConfig
) -> str | None:
    """The length limit, checked here so it holds even if no sweep is
    running. Returns what to say if the session was just handed off."""
    if config.timers is None:
        return None
    sentence = await timekeeper.hand_off_if_over_time(
        db,
        session_id,
        now=datetime.now(UTC),
        timers=config.timers,
        tax_rate=config.tax_rate,
    )
    await db.commit()
    return sentence


async def _converse(
    db: AsyncSession,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    model: ModelClient,
    config: AgentConfig,
) -> AgentReply:
    """Ask the model, run the tools it asks for, and repeat until it answers."""
    instructions = await build_instructions(db, config.restaurant_name)
    context = ToolContext(
        db,
        session_id,
        config.tax_rate,
        config.limits,
        config.reservation_ttl,
        specials_cache=config.specials_cache,
        knowledge=config.knowledge,
    )
    tools_used: list[str] = []
    reply: str | None = None

    for _ in range(config.max_tool_rounds):
        # The conversation is read from the event log each round. Nothing
        # about it is held in memory between requests.
        messages = await events.load_messages(db, session_id)
        started = time.perf_counter()
        with tracing.span(
            "model",
            OpenInferenceSpanKindValues.LLM,
            session_id=session_id,
            attributes=tracing.message_attributes(
                SpanAttributes.LLM_INPUT_MESSAGES, messages, system=instructions
            ),
        ) as model_span:
            turn = await model.complete(
                instructions=instructions, messages=messages, tools=TOOL_DEFINITIONS
            )
            model_span.set_attributes(tracing.model_turn_attributes(turn))
        await _record_model_call(db, session_id, turn_id, turn, started)

        if not turn.tool_calls:
            reply = turn.text or ""
            break

        for tool_call in turn.tool_calls:
            # Keyed by this turn and the model's own call id, so if the same
            # call is ever run again it takes effect once.
            call_context = replace(
                context, idempotency_key=f"{turn_id}:{tool_call.call_id}"
            )
            result = await run_tool(call_context, tool_call.name, tool_call.arguments)
            # The call and its result are recorded together, so the log never
            # holds a call without its answer.
            await events.record_message(db, session_id, turn_id, tool_call)
            await events.record_message(
                db,
                session_id,
                turn_id,
                ToolResult(tool_call.call_id, tool_call.name, result),
            )
            await db.commit()
            tools_used.append(tool_call.name)

    gave_up = reply is None
    text = GAVE_UP_REPLY if reply is None else reply
    await events.record_message(db, session_id, turn_id, AssistantMessage(text))
    await db.commit()

    return AgentReply(
        text=text,
        cart=await get_cart(db, session_id, tax_rate=config.tax_rate),
        turn_id=turn_id,
        tools_used=tuple(tools_used),
        gave_up=gave_up,
    )


async def _record_model_call(
    db: AsyncSession,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    turn: ModelTurn,
    started: float,
) -> None:
    await events.record(
        db,
        session_id,
        turn_id,
        AgentEventKind.MODEL_CALL,
        {
            "model": turn.model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "input_tokens": turn.usage.input_tokens if turn.usage else None,
            "output_tokens": turn.usage.output_tokens if turn.usage else None,
            "tool_calls": len(turn.tool_calls),
        },
    )
