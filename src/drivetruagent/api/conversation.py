"""The conversation endpoint: send what the customer said, get the reply."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.model import ModelClient, ModelUnavailable
from drivetruagent.agent.openai_model import OpenAIModel
from drivetruagent.api.deps import SettingsDep
from drivetruagent.api.errors import NOT_FOUND, UNAVAILABLE
from drivetruagent.config import Settings
from drivetruagent.db.session import SessionDep
from drivetruagent.ordering.views import CartSnapshot

router = APIRouter(prefix="/sessions/{session_id}", tags=["conversation"])


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


class MessageResponse(BaseModel):
    reply: str
    cart: CartSnapshot
    # The tools the agent used this turn, in order.
    tools_used: list[str]


def get_model_client(settings: SettingsDep) -> ModelClient:
    """The model the agent talks to. Tests replace this with a scripted one."""
    if settings.openai_api_key is None:
        raise ModelUnavailable("No model is configured. Set OPENAI_API_KEY.")
    return OpenAIModel(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_text_model,
    )


def agent_config(settings: Settings) -> AgentConfig:
    return AgentConfig(
        restaurant_name=settings.restaurant_name,
        tax_rate=settings.tax_rate,
        limits=settings.policy_limits,
        max_tool_rounds=settings.agent_max_tool_rounds,
    )


ModelDep = Annotated[ModelClient, Depends(get_model_client)]


@router.post(
    "/messages",
    responses=NOT_FOUND | UNAVAILABLE,
    summary="Say something to the agent",
)
async def send_message(
    session_id: uuid.UUID,
    body: MessageRequest,
    db: SessionDep,
    settings: SettingsDep,
    model: ModelDep,
) -> MessageResponse:
    """Send what the customer said and get the agent's reply.

    The agent may use several tools before it answers. Each tool's effect is
    saved as it happens, and the reply comes with the cart as it now stands.
    """
    reply = await run_turn(
        db, session_id, body.text, model=model, config=agent_config(settings)
    )
    return MessageResponse(
        reply=reply.text, cart=reply.cart, tools_used=list(reply.tools_used)
    )
