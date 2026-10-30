"""The span attributes and the tracing setup. No exporter is reached."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from drivetruagent.agent import tracing
from drivetruagent.agent.messages import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelTurn, ModelUsage
from drivetruagent.config import Settings
from drivetruagent.telemetry import (
    APP_TELEMETRY,
    build_provider,
    setup_tracing,
    untraced,
)


def settings(**values: Any) -> Settings:
    return Settings(database_url="unused", _env_file=None, **values)  # pyright: ignore[reportCallIssue]


# --- Values -----------------------------------------------------------------


def test_text_is_sent_as_text() -> None:
    assert tracing.value_attributes("input", "Hi") == {
        "input.value": "Hi",
        "input.mime_type": "text/plain",
    }


def test_anything_else_is_sent_as_json() -> None:
    assert tracing.value_attributes("output", {"decision": "allow", "é": 1}) == {
        "output.value": '{"decision": "allow", "é": 1}',
        "output.mime_type": "application/json",
    }


def test_a_long_value_is_cut_and_says_how_much_was_left_out() -> None:
    value = tracing.value_attributes("input", "x" * (tracing.MAX_VALUE_LENGTH + 5))[
        "input.value"
    ]

    assert isinstance(value, str)
    assert value.endswith("… [5 more]")
    assert len(value) < tracing.MAX_VALUE_LENGTH + 20


def test_metadata_is_one_json_attribute() -> None:
    assert tracing.metadata({"turn_id": "t1", "gave_up": False}) == {
        "metadata": '{"turn_id": "t1", "gave_up": false}'
    }


# --- Messages ---------------------------------------------------------------


def test_a_conversation_is_flattened_with_the_instructions_first() -> None:
    attributes = tracing.message_attributes(
        "llm.input_messages",
        [
            UserMessage("A cookie"),
            ToolCall("c1", "add_item", {"item_code": "cookie"}),
            ToolResult("c1", "add_item", {"decision": "allow"}),
            AssistantMessage("One cookie."),
        ],
        system="You take orders.",
    )

    assert attributes == {
        "llm.input_messages.0.message.role": "system",
        "llm.input_messages.0.message.content": "You take orders.",
        "llm.input_messages.1.message.role": "user",
        "llm.input_messages.1.message.content": "A cookie",
        "llm.input_messages.2.message.role": "assistant",
        "llm.input_messages.2.message.tool_calls.0.tool_call.id": "c1",
        "llm.input_messages.2.message.tool_calls.0.tool_call.function.name": "add_item",
        "llm.input_messages.2.message.tool_calls.0.tool_call.function.arguments": (
            '{"item_code": "cookie"}'
        ),
        "llm.input_messages.3.message.role": "tool",
        "llm.input_messages.3.message.tool_call_id": "c1",
        "llm.input_messages.3.message.name": "add_item",
        "llm.input_messages.3.message.content": '{"decision": "allow"}',
        "llm.input_messages.4.message.role": "assistant",
        "llm.input_messages.4.message.content": "One cookie.",
    }


def test_without_instructions_the_conversation_starts_at_zero() -> None:
    attributes = tracing.message_attributes("p", [UserMessage("Hi")])

    assert attributes == {"p.0.message.role": "user", "p.0.message.content": "Hi"}


def test_a_model_answer_carries_its_text_tool_calls_and_tokens() -> None:
    turn = ModelTurn(
        text="Sure.",
        tool_calls=(
            ToolCall("c1", "get_cart", {}),
            ToolCall("c2", "add_item", {"q": 1}),
        ),
        model="gpt-6-luna",
        usage=ModelUsage(input_tokens=100, output_tokens=20),
    )

    attributes = tracing.model_turn_attributes(turn)

    assert attributes["llm.model_name"] == "gpt-6-luna"
    assert attributes["llm.output_messages.0.message.content"] == "Sure."
    assert (
        attributes["llm.output_messages.0.message.tool_calls.1.tool_call.function.name"]
        == "add_item"
    )
    assert (
        attributes["llm.token_count.prompt"],
        attributes["llm.token_count.completion"],
        attributes["llm.token_count.total"],
    ) == (100, 20, 120)


def test_a_model_answer_without_usage_has_no_token_counts() -> None:
    attributes = tracing.model_turn_attributes(ModelTurn(text="Hi", model="demo"))

    assert not any(key.startswith("llm.token_count") for key in attributes)


def test_passages_become_retrieval_documents_with_the_rest_as_metadata() -> None:
    attributes = tracing.document_attributes(
        [{"id": "chunk-1", "score": 0.65, "text": "Canola oil.", "document": "fryer"}]
    )

    assert attributes == {
        "retrieval.documents.0.document.id": "chunk-1",
        "retrieval.documents.0.document.score": 0.65,
        "retrieval.documents.0.document.content": "Canola oil.",
        "retrieval.documents.0.document.metadata": '{"document": "fryer"}',
    }


# --- Setup ------------------------------------------------------------------


def test_with_no_endpoint_tracing_stays_off() -> None:
    assert setup_tracing(settings()) is None


def test_the_provider_names_the_service_and_the_phoenix_project() -> None:
    provider = build_provider(settings(tracing_project="demo"), InMemorySpanExporter())

    attributes = provider.resource.attributes
    assert attributes["service.name"] == "drivetruagent"
    assert attributes["openinference.project.name"] == "demo"
    provider.shutdown()


# --- Which requests FastAPI traces ------------------------------------------


def http(method: str, path: str) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": path}


@pytest.mark.parametrize(
    "scope",
    [
        http("GET", "/health/ready"),
        http("GET", "/health/live"),
        http("GET", "/costs/today"),
        http("GET", "/summaries"),
        http("HEAD", "/menu"),
        http("OPTIONS", "/sessions"),
    ],
)
def test_health_checks_and_reads_are_left_out(scope: dict[str, Any]) -> None:
    assert untraced(scope)


@pytest.mark.parametrize(
    "scope",
    [
        http("POST", "/sessions/abc/messages"),
        http("POST", "/web-phone/calls"),
        http("DELETE", "/sessions/abc"),
        {"type": "websocket", "path": "/voice/abc"},
    ],
)
def test_changes_and_voice_connections_are_traced(scope: dict[str, Any]) -> None:
    assert not untraced(scope)


def test_a_health_check_leaves_no_trace_but_a_turn_does() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI(telemetry={**APP_TELEMETRY, "tracer_provider": provider})

    @app.get("/health/ready")
    def ready() -> str:
        return "ok"

    @app.post("/sessions/{session_id}/messages")
    def message(session_id: str) -> str:
        return session_id

    with TestClient(app) as client:
        client.get("/health/ready")
        client.post("/sessions/abc/messages")

    names = [span.name for span in exporter.get_finished_spans()]
    assert "POST /sessions/{session_id}/messages" in names
    assert not [name for name in names if "health" in name]
    provider.shutdown()
