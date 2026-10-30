"""The conversation endpoint: send what the customer said, get the reply."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.agent import events
from drivetruagent.agent.factory import agent_config, agent_info, model_from_settings
from drivetruagent.agent.loop import run_turn
from drivetruagent.agent.model import ModelClient
from drivetruagent.api.deps import SessionDep, SettingsDep, SpecialsCacheDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND, UNAVAILABLE
from drivetruagent.enums import AgentEventKind
from drivetruagent.ordering.restaurant import registered_name
from drivetruagent.ordering.sessions import get_session as get_customer_session
from drivetruagent.ordering.views import CartSnapshot

router = APIRouter(tags=["conversation"])


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


class MessageResponse(BaseModel):
    reply: str
    cart: CartSnapshot
    # The tools the agent used this turn, in order.
    tools_used: list[str]


class AgentInfoResponse(BaseModel):
    restaurant_name: str
    # False when no model is configured; the conversation endpoint answers 503.
    available: bool
    model: str | None
    # True when the model is the fixed demo script, not a real model.
    scripted: bool
    # For the demo script: what to type at each turn.
    suggested_lines: list[str]
    # Whether voice calls can be made. They need an OpenAI key.
    voice_available: bool
    voice_model: str | None


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Always increasing. Pass the last one seen as `after` to get only newer events.
    seq: int
    turn_id: uuid.UUID
    kind: AgentEventKind
    payload: dict[str, Any]
    created_at: datetime


def get_model_client(settings: SettingsDep) -> ModelClient:
    """The model the agent talks to. Tests replace this with a scripted one."""
    return model_from_settings(settings)


ModelDep = Annotated[ModelClient, Depends(get_model_client)]


@router.post(
    "/sessions/{session_id}/messages",
    responses=NOT_FOUND | CONFLICT | UNAVAILABLE,
    summary="Say something to the agent",
)
async def send_message(
    session_id: uuid.UUID,
    body: MessageRequest,
    db: SessionDep,
    settings: SettingsDep,
    model: ModelDep,
    specials_cache: SpecialsCacheDep,
) -> MessageResponse:
    """Send what the customer said and get the agent's reply.

    The agent may use several tools before it answers. Each tool's effect is
    saved as it happens, and the reply comes with the cart as it now stands.
    A session ended for abuse takes no more messages (409).
    """
    reply = await run_turn(
        db,
        session_id,
        body.text,
        model=model,
        config=agent_config(settings, specials_cache=specials_cache),
    )
    return MessageResponse(
        reply=reply.text, cart=reply.cart, tools_used=list(reply.tools_used)
    )


@router.get(
    "/sessions/{session_id}/events",
    responses=NOT_FOUND,
    summary="What has happened in a session",
)
async def read_events(
    session_id: uuid.UUID,
    db: SessionDep,
    after: Annotated[
        int | None,
        Query(description="Return only events with a higher sequence number."),
    ] = None,
) -> list[EventView]:
    """The session's event log, oldest first: every message, model call, tool
    call and tool result. A tool result carries the policy decision and the
    cart as it stood, so a screen can follow a turn step by step."""
    await get_customer_session(db, session_id)  # 404 for an unknown session
    return [
        EventView.model_validate(event)
        for event in await events.load_events(db, session_id, after)
    ]


@router.get("/agent", summary="Which model the agent uses")
async def read_agent_info(db: SessionDep, settings: SettingsDep) -> AgentInfoResponse:
    info = agent_info(settings)
    return AgentInfoResponse(
        restaurant_name=await registered_name(db, settings.restaurant_name),
        available=info.available,
        model=info.model,
        scripted=info.scripted,
        suggested_lines=list(info.suggested_lines),
        voice_available=settings.openai_api_key is not None,
        voice_model=settings.voice_model if settings.openai_api_key else None,
    )
