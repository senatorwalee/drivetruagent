"""Builds the agent's model and configuration from the app settings.

Used by every way into the agent: the API endpoint, the terminal chat and
the evals. None of them needs to know how a model is constructed.
"""

from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.model import ModelClient, ModelUnavailable
from drivetruagent.agent.openai_model import OpenAIModel
from drivetruagent.config import Settings


def model_from_settings(settings: Settings) -> ModelClient:
    """The model the agent talks to. Raises ModelUnavailable without a key."""
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
