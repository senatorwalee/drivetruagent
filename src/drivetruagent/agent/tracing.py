"""Spans for Arize Phoenix, in OpenInference's conventions.

The event log is the record of what happened. Tracing is a view of the same
work, made for looking at it: each turn as a timeline, with the model calls,
tools, guardrail checks and knowledge searches nested under it and timed.

Nothing here decides anything, and with no tracer provider set up (the
default) every span is a no-op. The attribute builders are plain functions.
"""

import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from openinference.semconv.trace import (
    DocumentAttributes,
    MessageAttributes,
    OpenInferenceMimeTypeValues,
    OpenInferenceSpanKindValues,
    SpanAttributes,
    ToolCallAttributes,
)
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from drivetruagent.agent.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelTurn

TRACER_NAME = "drivetruagent"
# Long values are cut, so one tool result cannot make a span huge.
MAX_VALUE_LENGTH = 8000

type Attributes = dict[str, str | int | float | bool]

_tracer = trace.get_tracer(TRACER_NAME)


@contextmanager
def span(
    name: str,
    kind: OpenInferenceSpanKindValues,
    *,
    session_id: uuid.UUID,
    input_value: object = None,
    attributes: Attributes | None = None,
) -> Iterator[Span]:
    """A span of an OpenInference kind, tied to the session. An exception
    leaving it marks it as an error and is raised again."""
    with _tracer.start_as_current_span(
        name, record_exception=True, set_status_on_exception=True
    ) as current:
        current.set_attributes(
            {
                SpanAttributes.OPENINFERENCE_SPAN_KIND: kind.value,
                SpanAttributes.SESSION_ID: str(session_id),
                **(
                    value_attributes("input", input_value)
                    if input_value is not None
                    else {}
                ),
                **(attributes or {}),
            }
        )
        yield current
        # Finished without raising: OK, unless set_error said otherwise.
        status = getattr(current, "status", None)
        if status is None or status.status_code is StatusCode.UNSET:
            current.set_status(Status(StatusCode.OK))


def set_output(current: Span, value: object) -> None:
    current.set_attributes(value_attributes("output", value))


def set_error(current: Span, message: str) -> None:
    current.set_status(Status(StatusCode.ERROR, message))


# --- Attribute builders -----------------------------------------------------


def value_attributes(direction: str, value: object) -> Attributes:
    """`input.value` or `output.value`: text as it is, anything else as JSON."""
    if isinstance(value, str):
        return {
            f"{direction}.value": _cut(value),
            f"{direction}.mime_type": OpenInferenceMimeTypeValues.TEXT.value,
        }
    return {
        f"{direction}.value": _cut(_json(value)),
        f"{direction}.mime_type": OpenInferenceMimeTypeValues.JSON.value,
    }


def metadata(values: Mapping[str, Any]) -> Attributes:
    return {SpanAttributes.METADATA: _cut(_json(values))}


def message_attributes(
    prefix: str, messages: Sequence[Message], *, system: str | None = None
) -> Attributes:
    """A conversation flattened as `llm.input_messages.N.message.*`, with the
    instructions first as the system message when given."""
    attributes: Attributes = {}
    offset = 0
    if system is not None:
        attributes[f"{prefix}.0.{MessageAttributes.MESSAGE_ROLE}"] = "system"
        attributes[f"{prefix}.0.{MessageAttributes.MESSAGE_CONTENT}"] = _cut(system)
        offset = 1
    for index, message in enumerate(messages, start=offset):
        key = f"{prefix}.{index}."
        if isinstance(message, UserMessage):
            attributes[key + MessageAttributes.MESSAGE_ROLE] = "user"
            attributes[key + MessageAttributes.MESSAGE_CONTENT] = _cut(message.text)
        elif isinstance(message, AssistantMessage):
            attributes[key + MessageAttributes.MESSAGE_ROLE] = "assistant"
            attributes[key + MessageAttributes.MESSAGE_CONTENT] = _cut(message.text)
        elif isinstance(message, ToolCall):
            attributes[key + MessageAttributes.MESSAGE_ROLE] = "assistant"
            attributes.update(_tool_call_attributes(key, 0, message))
        elif isinstance(message, ToolResult):
            attributes[key + MessageAttributes.MESSAGE_ROLE] = "tool"
            attributes[key + MessageAttributes.MESSAGE_TOOL_CALL_ID] = message.call_id
            attributes[key + MessageAttributes.MESSAGE_NAME] = message.name
            attributes[key + MessageAttributes.MESSAGE_CONTENT] = _cut(
                _json(message.result)
            )
    return attributes


def model_turn_attributes(turn: ModelTurn) -> Attributes:
    """What the model answered: its text and tool calls as one output
    message, the model's name and the token counts."""
    key = f"{SpanAttributes.LLM_OUTPUT_MESSAGES}.0."
    attributes: Attributes = {
        SpanAttributes.LLM_MODEL_NAME: turn.model,
        key + MessageAttributes.MESSAGE_ROLE: "assistant",
    }
    if turn.text:
        attributes[key + MessageAttributes.MESSAGE_CONTENT] = _cut(turn.text)
    for index, call in enumerate(turn.tool_calls):
        attributes.update(_tool_call_attributes(key, index, call))
    if turn.usage is not None:
        attributes[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] = turn.usage.input_tokens
        attributes[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] = turn.usage.output_tokens
        attributes[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] = (
            turn.usage.input_tokens + turn.usage.output_tokens
        )
    return attributes


def document_attributes(documents: Sequence[Mapping[str, Any]]) -> Attributes:
    """Passages found by a search, as `retrieval.documents.N.document.*`.
    Each needs an id, a score and its text; anything else is metadata."""
    attributes: Attributes = {}
    for index, document in enumerate(documents):
        key = f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.{index}."
        attributes[key + DocumentAttributes.DOCUMENT_ID] = str(document["id"])
        attributes[key + DocumentAttributes.DOCUMENT_SCORE] = float(document["score"])
        attributes[key + DocumentAttributes.DOCUMENT_CONTENT] = _cut(
            str(document["text"])
        )
        extra = {k: v for k, v in document.items() if k not in {"id", "score", "text"}}
        if extra:
            attributes[key + DocumentAttributes.DOCUMENT_METADATA] = _json(extra)
    return attributes


def _tool_call_attributes(message_key: str, index: int, call: ToolCall) -> Attributes:
    key = f"{message_key}{MessageAttributes.MESSAGE_TOOL_CALLS}.{index}."
    return {
        key + ToolCallAttributes.TOOL_CALL_ID: call.call_id,
        key + ToolCallAttributes.TOOL_CALL_FUNCTION_NAME: call.name,
        key + ToolCallAttributes.TOOL_CALL_FUNCTION_ARGUMENTS_JSON: _cut(
            _json(call.arguments)
        ),
    }


def _json(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _cut(text: str) -> str:
    if len(text) <= MAX_VALUE_LENGTH:
        return text
    return text[:MAX_VALUE_LENGTH] + f"… [{len(text) - MAX_VALUE_LENGTH} more]"
