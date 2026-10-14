"""Which model the settings select. No database, and no model is called."""

from typing import Any

import pytest

from drivetruagent.agent.demo_model import DemoModel
from drivetruagent.agent.factory import (
    agent_info,
    embedder_from_settings,
    guard_from_settings,
    ingest_embedders,
    knowledge_from_settings,
    model_from_settings,
)
from drivetruagent.agent.gemini_model import GeminiModel
from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.agent.openai_model import OpenAIEmbedder, OpenAIModel
from drivetruagent.config import Settings
from drivetruagent.knowledge.embedding import HashingEmbedder
from drivetruagent.policy.guardrails import StrikeLimits


def settings(**values: Any) -> Settings:
    """Settings from the given values alone: no environment, no .env file."""
    values = {
        "database_url": "postgresql+psycopg://unused",
        "_env_file": None,
        **values,
    }
    return Settings(**values)


# The keys below are placeholders. No request is made with them.
WITH_OPENAI = {"openai_api_key": "test-openai-key"}
WITH_GEMINI = {"agent_model": "gemini", "gemini_api_key": "test-gemini-key"}


def test_openai_is_the_default_provider() -> None:
    assert settings().agent_model == "openai"
    assert isinstance(model_from_settings(settings(**WITH_OPENAI)), OpenAIModel)


def test_gemini_is_selected_by_a_setting() -> None:
    assert isinstance(model_from_settings(settings(**WITH_GEMINI)), GeminiModel)


def test_the_demo_needs_no_key() -> None:
    assert isinstance(model_from_settings(settings(agent_model="demo")), DemoModel)


def test_gemini_without_its_key_is_unavailable_and_says_which_key() -> None:
    with pytest.raises(ModelUnavailable, match="GEMINI_API_KEY"):
        model_from_settings(settings(agent_model="gemini"))


def test_an_openai_key_does_not_stand_in_for_a_gemini_key() -> None:
    """Choosing Gemini with only an OpenAI key set must not quietly use OpenAI:
    the person asked for the free one."""
    with pytest.raises(ModelUnavailable, match="GEMINI_API_KEY"):
        model_from_settings(settings(agent_model="gemini", **WITH_OPENAI))


def test_a_gemini_key_alone_does_not_switch_provider() -> None:
    with pytest.raises(ModelUnavailable, match="OPENAI_API_KEY"):
        model_from_settings(settings(gemini_api_key="test-gemini-key"))


def test_with_no_key_the_message_lists_every_way_to_get_going() -> None:
    with pytest.raises(ModelUnavailable) as error:
        model_from_settings(settings())

    for way in ("OPENAI_API_KEY", "AGENT_MODEL=gemini", "AGENT_MODEL=demo"):
        assert way in str(error.value)


def test_agent_info_names_the_gemini_model() -> None:
    info = agent_info(settings(**WITH_GEMINI))

    assert (info.available, info.model, info.scripted) == (
        True,
        "gemini-2.5-flash-lite",
        False,
    )
    assert info.suggested_lines == ()


def test_agent_info_follows_the_model_setting() -> None:
    chosen = settings(**WITH_GEMINI, gemini_text_model="gemini-2.5-flash")

    assert agent_info(chosen).model == "gemini-2.5-flash"


def test_agent_info_says_gemini_is_unavailable_without_its_key() -> None:
    info = agent_info(settings(agent_model="gemini", **WITH_OPENAI))

    assert (info.available, info.model) == (False, None)


def test_agent_info_for_openai() -> None:
    assert agent_info(settings(**WITH_OPENAI)).model == "gpt-6-luna"
    assert agent_info(settings()).available is False


def test_keys_are_not_shown_when_settings_are_printed() -> None:
    shown = repr(settings(**WITH_GEMINI, **WITH_OPENAI))

    assert "test-gemini-key" not in shown
    assert "test-openai-key" not in shown


# --- The guardrail ----------------------------------------------------------


def test_with_a_real_model_the_guard_has_a_classifier() -> None:
    guard = guard_from_settings(settings(**WITH_OPENAI))

    assert guard.classifier is not None
    assert guard.limits == StrikeLimits(abuse=3, confusion=3)


@pytest.mark.parametrize(
    "values",
    [
        {"agent_model": "demo"},
        {**WITH_OPENAI, "guardrail_classifier": False},
        {"agent_model": "gemini"},  # no key: the model is unavailable
    ],
)
def test_otherwise_only_the_rules_apply(values: dict[str, Any]) -> None:
    assert guard_from_settings(settings(**values)).classifier is None


def test_the_strike_limits_come_from_the_settings() -> None:
    guard = guard_from_settings(
        settings(
            agent_model="demo",
            guardrail_abuse_strikes=2,
            guardrail_confusion_strikes=5,
        )
    )

    assert guard.limits == StrikeLimits(abuse=2, confusion=5)


# --- Knowledge search -------------------------------------------------------


def test_with_an_openai_key_openai_embeds_the_questions() -> None:
    chosen = embedder_from_settings(settings(**WITH_OPENAI))

    assert isinstance(chosen, OpenAIEmbedder)
    assert chosen.model == "text-embedding-3-small"


@pytest.mark.parametrize(
    "values", [{"agent_model": "demo", **WITH_OPENAI}, WITH_GEMINI, {}]
)
def test_otherwise_the_local_hashing_embedder_is_used(values: dict[str, Any]) -> None:
    assert isinstance(embedder_from_settings(settings(**values)), HashingEmbedder)


def test_ingest_always_indexes_with_hashing_and_with_openai_when_it_can() -> None:
    assert [e.model for e in ingest_embedders(settings())] == ["hashing-v1"]
    assert [e.model for e in ingest_embedders(settings(**WITH_OPENAI))] == [
        "hashing-v1",
        "text-embedding-3-small",
    ]


def test_the_search_threshold_and_size_come_from_the_settings() -> None:
    search = knowledge_from_settings(
        settings(agent_model="demo", knowledge_min_score=0.5, knowledge_results=5)
    )

    assert (search.threshold, search.limit) == (0.5, 5)
    assert knowledge_from_settings(settings(agent_model="demo")).threshold == (
        HashingEmbedder.min_score
    )
