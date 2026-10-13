"""Writing and reading the agent's event log.

The log is the conversation. Nothing about a conversation is kept in the
app's memory: each model call rebuilds its context from these rows, so any
server process can serve any turn.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.db.models import AgentEvent
from drivetruagent.enums import AgentEventKind

# The kinds that are part of what the model sees. A model_call event is
# bookkeeping about a request, not part of the conversation.
_CONVERSATION_KINDS = (
    AgentEventKind.USER_MESSAGE,
    AgentEventKind.ASSISTANT_MESSAGE,
    AgentEventKind.TOOL_CALL,
    AgentEventKind.TOOL_RESULT,
)


async def record(
    db: AsyncSession,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    kind: AgentEventKind,
    payload: dict[str, Any],
) -> None:
    db.add(
        AgentEvent(session_id=session_id, turn_id=turn_id, kind=kind, payload=payload)
    )
    await db.flush()


async def record_message(
    db: AsyncSession, session_id: uuid.UUID, turn_id: uuid.UUID, message: Message
) -> None:
    kind, payload = _to_event(message)
    await record(db, session_id, turn_id, kind, payload)


async def load_messages(db: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    """The whole conversation so far, oldest first."""
    statement = (
        select(AgentEvent)
        .where(
            AgentEvent.session_id == session_id,
            AgentEvent.kind.in_(_CONVERSATION_KINDS),
        )
        .order_by(AgentEvent.seq)
    )
    events = (await db.execute(statement)).scalars()
    return [_to_message(event) for event in events]


async def load_events(
    db: AsyncSession, session_id: uuid.UUID, after: int | None = None
) -> list[AgentEvent]:
    """A session's events, oldest first: the full audit trail.

    With `after`, only events newer than that sequence number. A screen that
    is following along asks for what it has not seen yet.
    """
    statement = (
        select(AgentEvent)
        .where(AgentEvent.session_id == session_id)
        .order_by(AgentEvent.seq)
    )
    if after is not None:
        statement = statement.where(AgentEvent.seq > after)
    return list((await db.execute(statement)).scalars())


def _to_event(message: Message) -> tuple[AgentEventKind, dict[str, Any]]:
    if isinstance(message, UserMessage):
        return AgentEventKind.USER_MESSAGE, {"text": message.text}
    if isinstance(message, AssistantMessage):
        return AgentEventKind.ASSISTANT_MESSAGE, {"text": message.text}
    if isinstance(message, ToolCall):
        return AgentEventKind.TOOL_CALL, {
            "call_id": message.call_id,
            "name": message.name,
            "arguments": message.arguments,
        }
    return AgentEventKind.TOOL_RESULT, {
        "call_id": message.call_id,
        "name": message.name,
        "result": message.result,
    }


def _to_message(event: AgentEvent) -> Message:
    payload = event.payload
    if event.kind is AgentEventKind.USER_MESSAGE:
        return UserMessage(text=payload["text"])
    if event.kind is AgentEventKind.ASSISTANT_MESSAGE:
        return AssistantMessage(text=payload["text"])
    if event.kind is AgentEventKind.TOOL_CALL:
        return ToolCall(
            call_id=payload["call_id"],
            name=payload["name"],
            arguments=payload["arguments"],
        )
    return ToolResult(
        call_id=payload["call_id"], name=payload["name"], result=payload["result"]
    )
