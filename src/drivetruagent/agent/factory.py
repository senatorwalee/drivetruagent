"""Builds the agent's model and configuration from the app settings.

Used by every way into the agent: the API endpoint, the terminal chat and
the evals. None of them needs to know how a model is constructed.
"""

from dataclasses import dataclass

from drivetruagent.agent import demo_model
from drivetruagent.agent.demo_model import DemoModel
from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.model import ModelClient, ModelUnavailable
from drivetruagent.agent.openai_model import OpenAIModel
from drivetruagent.config import Settings


@dataclass(frozen=True)
class AgentInfo:
    """What a client needs to know about the agent before talking to it."""

    available: bool
    # The model's name, or None when nothing is configured.
    model: str | None
    # True when the model is the fixed demo script, not a real model.
    scripted: bool
    # For the demo script: what to type at each turn. Empty otherwise.
    suggested_lines: tuple[str, ...]


def model_from_settings(settings: Settings) -> ModelClient:
    """The model the agent talks to. Raises ModelUnavailable if none is set up."""
    if settings.agent_model == "demo":
        return DemoModel()
    if settings.openai_api_key is None:
        raise ModelUnavailable(
            "No model is configured. Set OPENAI_API_KEY, or set AGENT_MODEL=demo "
            "to use the scripted demo."
        )
    return OpenAIModel(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_text_model,
    )


def agent_info(settings: Settings) -> AgentInfo:
    if settings.agent_model == "demo":
        return AgentInfo(True, demo_model.MODEL_NAME, True, demo_model.SUGGESTED_LINES)
    if settings.openai_api_key is None:
        return AgentInfo(False, None, False, ())
    return AgentInfo(True, settings.openai_text_model, False, ())


def agent_config(settings: Settings) -> AgentConfig:
    return AgentConfig(
        restaurant_name=settings.restaurant_name,
        tax_rate=settings.tax_rate,
        limits=settings.policy_limits,
        max_tool_rounds=settings.agent_max_tool_rounds,
    )
