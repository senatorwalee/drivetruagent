"""Builds the agent's model and configuration from the app settings.

Used by every way into the agent: the API endpoint, the terminal chat and
the evals. None of them needs to know how a model is constructed.
"""

from dataclasses import dataclass

from drivetruagent.agent import demo_model
from drivetruagent.agent.demo_model import DemoModel
from drivetruagent.agent.gemini_model import GeminiModel
from drivetruagent.agent.guard import Guard, ModelClassifier
from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.model import ModelClient, ModelUnavailable
from drivetruagent.agent.openai_model import OpenAIEmbedder, OpenAIModel
from drivetruagent.config import Settings
from drivetruagent.knowledge.embedding import Embedder, HashingEmbedder
from drivetruagent.knowledge.store import KnowledgeSearch
from drivetruagent.ordering.offers import SpecialsCache
from drivetruagent.policy.guardrails import StrikeLimits


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
    if settings.agent_model == "gemini":
        if settings.gemini_api_key is None:
            raise ModelUnavailable(
                "AGENT_MODEL is gemini but GEMINI_API_KEY is not set. Add the "
                "key, or set AGENT_MODEL=demo to use the scripted demo."
            )
        return GeminiModel(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_text_model,
        )
    if settings.openai_api_key is None:
        raise ModelUnavailable(
            "No model is configured. Set OPENAI_API_KEY, set AGENT_MODEL=gemini "
            "with GEMINI_API_KEY, or set AGENT_MODEL=demo to use the scripted demo."
        )
    return OpenAIModel(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_text_model,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def classifier_model_from_settings(settings: Settings) -> ModelClient:
    """The model the guardrail classifier asks. The agent's, but with no
    reasoning on OpenAI: picking a label needs none, and it runs every turn."""
    model = model_from_settings(settings)
    if isinstance(model, OpenAIModel):
        return model.with_reasoning_effort("none")
    return model


def summary_model_from_settings(settings: Settings) -> ModelClient | None:
    """The model asked for session summaries' words, or None to use code's
    own wording: turned off, the scripted demo, or no model set up. The
    agent's model with no reasoning on OpenAI: a note needs none."""
    if not settings.summary_uses_model or settings.agent_model == "demo":
        return None
    try:
        return classifier_model_from_settings(settings)
    except ModelUnavailable:
        return None


def agent_info(settings: Settings) -> AgentInfo:
    if settings.agent_model == "demo":
        return AgentInfo(True, demo_model.MODEL_NAME, True, demo_model.SUGGESTED_LINES)
    if settings.agent_model == "gemini":
        if settings.gemini_api_key is None:
            return AgentInfo(False, None, False, ())
        return AgentInfo(True, settings.gemini_text_model, False, ())
    if settings.openai_api_key is None:
        return AgentInfo(False, None, False, ())
    return AgentInfo(True, settings.openai_text_model, False, ())


def agent_config(
    settings: Settings, *, specials_cache: SpecialsCache | None = None
) -> AgentConfig:
    return AgentConfig(
        restaurant_name=settings.restaurant_name,
        tax_rate=settings.tax_rate,
        limits=settings.policy_limits,
        max_tool_rounds=settings.agent_max_tool_rounds,
        reservation_ttl=settings.reservation_ttl,
        specials_cache=specials_cache,
        timers=settings.session_timers,
        guard=guard_from_settings(settings),
        guard_first=settings.guardrail_check_first,
        knowledge=knowledge_from_settings(settings),
        pickup_minutes=settings.pickup_ready_minutes,
    )


def embedder_from_settings(settings: Settings) -> Embedder:
    """OpenAI's embedder when there is a key and a real model is in use;
    otherwise the local hashing one, which needs nothing and costs nothing."""
    if settings.agent_model != "demo" and settings.openai_api_key is not None:
        return OpenAIEmbedder(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.embedding_model,
        )
    return HashingEmbedder()


def ingest_embedders(settings: Settings) -> list[Embedder]:
    """Every embedder documents are indexed with: the hashing one always, so
    the no-key demo can search, and OpenAI's too when there is a key."""
    embedders: list[Embedder] = [HashingEmbedder()]
    if settings.openai_api_key is not None:
        embedders.append(
            OpenAIEmbedder(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.embedding_model,
            )
        )
    return embedders


def knowledge_from_settings(settings: Settings) -> KnowledgeSearch:
    return KnowledgeSearch(
        embedder_from_settings(settings),
        limit=settings.knowledge_results,
        min_score=settings.knowledge_min_score,
    )


def guard_from_settings(settings: Settings) -> Guard:
    """The rules always; a classifier call too when a real model is set up.

    The scripted demo has no model to ask, so it gets the rules alone. So
    does a session whose model is unavailable: the agent will say so anyway.
    """
    limits = StrikeLimits(
        abuse=settings.guardrail_abuse_strikes,
        confusion=settings.guardrail_confusion_strikes,
    )
    if not settings.guardrail_classifier or settings.agent_model == "demo":
        return Guard(limits)
    try:
        return Guard(limits, ModelClassifier(classifier_model_from_settings(settings)))
    except ModelUnavailable:
        return Guard(limits)
