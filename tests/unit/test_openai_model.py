"""The OpenAI adapter, without calling OpenAI.

The conversions are pure functions. The request itself is checked by handing
the adapter a stand-in client that records what it was asked to send.
"""

import json
from types import SimpleNamespace
from typing import Any

import openai
import pytest

from drivetruagent.agent.messages import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelUnavailable, ModelUsage, ToolDefinition
from drivetruagent.agent.openai_model import (
    UNPARSEABLE_ARGUMENTS,
    OpenAIModel,
    from_response,
    to_input_items,
    to_tool_params,
)

TOOL = ToolDefinition(
    name="remove_item",
    description="Remove a line from the order.",
    parameters={
        "type": "object",
        "properties": {"line_no": {"type": "integer"}},
        "required": ["line_no"],
        "additionalProperties": False,
    },
)


def function_call(name: str, arguments: str, call_id: str = "call_1") -> Any:
    return SimpleNamespace(
        type="function_call", call_id=call_id, name=name, arguments=arguments
    )


def response(
    *items: Any, text: str = "", usage: tuple[int, int] | None = (120, 15)
) -> Any:
    """Shaped like the SDK's Response, with only the fields the adapter reads."""
    return SimpleNamespace(
        output=list(items),
        output_text=text,
        model="gpt-test",
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1])
        if usage
        else None,
    )


class RecordingClient:
    """Stands in for AsyncOpenAI: records the request, returns a canned reply."""

    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.request: dict[str, Any] = {}
        self._reply = reply
        self._error = error
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs: Any) -> Any:
        self.request = kwargs
        if self._error:
            raise self._error
        return self._reply


# --- To OpenAI's format -----------------------------------------------------


def test_a_conversation_becomes_input_items_in_order() -> None:
    items = to_input_items(
        [
            UserMessage("Deux frites, s'il vous plaît"),
            ToolCall("call_1", "search_menu", {"query": "frites"}),
            ToolResult("call_1", "search_menu", {"matches": [{"item_code": "fries"}]}),
            AssistantMessage("Quel format?"),
        ]
    )

    assert items == [
        {"role": "user", "content": "Deux frites, s'il vous plaît"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search_menu",
            "arguments": '{"query": "frites"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"matches": [{"item_code": "fries"}]}',
        },
        {"role": "assistant", "content": "Quel format?"},
    ]


def test_accents_are_sent_as_they_are() -> None:
    (item,) = to_input_items([ToolResult("c", "t", {"name": "Thé glacé"})])

    assert "Thé glacé" in item["output"]


def test_a_tool_result_is_tied_to_its_call_by_call_id() -> None:
    call, result = to_input_items(
        [ToolCall("call_7", "get_cart", {}), ToolResult("call_7", "get_cart", {})]
    )

    assert call["call_id"] == result["call_id"] == "call_7"


def test_tools_are_sent_as_strict_functions() -> None:
    assert to_tool_params([TOOL]) == [
        {
            "type": "function",
            "name": "remove_item",
            "description": "Remove a line from the order.",
            "parameters": TOOL.parameters,
            "strict": True,
        }
    ]


# --- From OpenAI's format ---------------------------------------------------


def test_a_text_reply_is_read() -> None:
    turn = from_response(response(text="What size would you like?"))

    assert turn.text == "What size would you like?"
    assert turn.tool_calls == ()
    assert turn.model == "gpt-test"
    assert turn.usage == ModelUsage(input_tokens=120, output_tokens=15)


def test_tool_calls_are_read_with_parsed_arguments() -> None:
    turn = from_response(
        response(
            function_call("add_item", '{"item_code": "cookie", "quantity": 2}', "a"),
            function_call("get_cart", "{}", "b"),
        )
    )

    assert turn.text is None
    assert turn.tool_calls == (
        ToolCall("a", "add_item", {"item_code": "cookie", "quantity": 2}),
        ToolCall("b", "get_cart", {}),
    )


def test_other_output_items_are_ignored() -> None:
    reasoning = SimpleNamespace(type="reasoning")
    message = SimpleNamespace(type="message")

    turn = from_response(response(reasoning, message, text="Hello!"))

    assert turn.tool_calls == ()
    assert turn.text == "Hello!"


def test_empty_arguments_mean_no_arguments() -> None:
    turn = from_response(response(function_call("get_cart", "")))

    assert turn.tool_calls[0].arguments == {}


@pytest.mark.parametrize("raw", ['{"item_code": ', "[1, 2]", '"just text"'])
def test_arguments_that_are_not_a_json_object_are_kept_for_rejection(raw: str) -> None:
    """They are passed on under a key no tool accepts, so the tool layer
    answers the model with invalid_tool_call instead of crashing."""
    turn = from_response(response(function_call("add_item", raw)))

    assert turn.tool_calls[0].arguments == {UNPARSEABLE_ARGUMENTS: raw}


def test_missing_usage_is_tolerated() -> None:
    assert from_response(response(text="Hi", usage=None)).usage is None


# --- The request ------------------------------------------------------------


async def test_complete_sends_the_whole_conversation_and_stores_nothing() -> None:
    client = RecordingClient(reply=response(text="Hi there!"))
    model = OpenAIModel(api_key="unused", model="gpt-test", client=client)  # pyright: ignore[reportArgumentType]

    turn = await model.complete(
        instructions="Take orders.", messages=[UserMessage("Hello")], tools=[TOOL]
    )

    assert turn.text == "Hi there!"
    assert client.request["model"] == "gpt-test"
    assert client.request["instructions"] == "Take orders."
    assert client.request["input"] == [{"role": "user", "content": "Hello"}]
    assert client.request["tools"][0]["name"] == "remove_item"
    # The event log is the only record of the conversation.
    assert client.request["store"] is False
    # The request must be something that can be sent as JSON.
    json.dumps(client.request)


async def test_a_provider_failure_becomes_model_unavailable() -> None:
    client = RecordingClient(error=openai.OpenAIError("connection refused"))
    model = OpenAIModel(api_key="unused", model="gpt-test", client=client)  # pyright: ignore[reportArgumentType]

    with pytest.raises(ModelUnavailable, match="connection refused"):
        await model.complete(instructions="", messages=[], tools=[])
