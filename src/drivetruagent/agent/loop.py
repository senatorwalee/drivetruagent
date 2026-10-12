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
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.messages import AssistantMessage, ToolResult, UserMessage
from drivetruagent.agent.model import ModelClient, ModelTurn
from drivetruagent.agent.prompt import build_instructions
from drivetruagent.agent.tools import TOOL_DEFINITIONS, ToolContext, run_tool
from drivetruagent.enums import AgentEventKind
from drivetruagent.ordering.cart import get_cart
from drivetruagent.ordering.sessions import get_session
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
    """Handle one thing the customer said and return what to say back."""
    await get_session(db, session_id)  # raises SessionNotFound
    turn_id = uuid.uuid4()
    await events.record_message(db, session_id, turn_id, UserMessage(user_text))
    await db.commit()

    instructions = await build_instructions(db, config.restaurant_name)
    context = ToolContext(db, session_id, config.tax_rate, config.limits)
    tools_used: list[str] = []
    reply: str | None = None

    for _ in range(config.max_tool_rounds):
        # The conversation is read from the event log each round. Nothing
        # about it is held in memory between requests.
        messages = await events.load_messages(db, session_id)
        started = time.perf_counter()
        turn = await model.complete(
            instructions=instructions, messages=messages, tools=TOOL_DEFINITIONS
        )
        await _record_model_call(db, session_id, turn_id, turn, started)

        if not turn.tool_calls:
            reply = turn.text or ""
            break

        for tool_call in turn.tool_calls:
            result = await run_tool(context, tool_call.name, tool_call.arguments)
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
