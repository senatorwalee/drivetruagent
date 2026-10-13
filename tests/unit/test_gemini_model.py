"""The Gemini adapter, without calling Google.

The conversions are pure functions over the SDK's own types, so a wrong field
name fails here (and in the type checker), not in front of a customer. The
request itself is checked by handing the adapter a stand-in client that
records what it was asked to send.
"""

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types

from drivetruagent.agent.gemini_model import (
    GeminiModel,
    from_response,
    to_contents,
    to_tools,
)
from drivetruagent.agent.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelUnavailable, ModelUsage, ToolDefinition
from drivetruagent.agent.tools import TOOL_DEFINITIONS

SIGNATURE = b"\x00an-encrypted-note\xff"
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


def call(
    call_id: str,
    name: str = "add_item",
    *,
    response_id: str | None = "resp-1",
    gemini_id: str | None = None,
    signature: bytes | None = None,
    **arguments: Any,
) -> ToolCall:
    """A tool call as the adapter records it. `response_id=None` makes one
    recorded by another provider, with no provider data at all."""
    if response_id is None:
        return ToolCall(call_id, name, arguments)
    return ToolCall(
        call_id,
        name,
        arguments,
        provider_data={
            "provider": "gemini",
            "response_id": response_id,
            "function_call_id": gemini_id,
            "thought_signature": base64.b64encode(signature).decode()
            if signature
            else None,
        },
    )


def result(call_id: str, name: str = "add_item", **value: Any) -> ToolResult:
    return ToolResult(call_id, name, value or {"decision": "allow"})


def shape(contents: list[types.Content]) -> list[tuple[str | None, list[str]]]:
    """Each turn as (role, what its parts are), for comparing structure."""

    def describe(part: types.Part) -> str:
        if part.function_call:
            return f"call:{part.function_call.name}"
        if part.function_response:
            return f"result:{part.function_response.name}"
        return f"text:{part.text}"

    return [(c.role, [describe(p) for p in c.parts or []]) for c in contents]


# --- Our messages -> Gemini's contents --------------------------------------


def test_the_customer_is_user_and_the_agent_is_model() -> None:
    contents = to_contents([UserMessage("Hi"), AssistantMessage("Welcome!")])

    assert shape(contents) == [("user", ["text:Hi"]), ("model", ["text:Welcome!"])]


def test_a_tool_call_is_a_model_turn_and_its_result_a_user_turn() -> None:
    messages: list[Message] = [
        UserMessage("Large fries"),
        call("c1", item_code="fries", quantity=1),
        result("c1", decision="allow", cart={"version": 1}),
        AssistantMessage("Large fries. Anything else?"),
    ]

    contents = to_contents(messages)

    assert shape(contents) == [
        ("user", ["text:Large fries"]),
        ("model", ["call:add_item"]),
        ("user", ["result:add_item"]),
        ("model", ["text:Large fries. Anything else?"]),
    ]
    asked = contents[1].parts[0].function_call  # pyright: ignore[reportOptionalSubscript]
    answered = contents[2].parts[0].function_response  # pyright: ignore[reportOptionalSubscript]
    assert asked.args == {"item_code": "fries", "quantity": 1}  # pyright: ignore[reportOptionalMemberAccess]
    assert answered.response == {"decision": "allow", "cart": {"version": 1}}  # pyright: ignore[reportOptionalMemberAccess]


def test_calls_made_in_one_response_go_back_together() -> None:
    """The log records call, result, call, result. Gemini wants both calls in
    one model turn, then both results in one user turn."""
    messages: list[Message] = [
        UserMessage("Fries and a cola"),
        call("a", item_code="fries"),
        result("a"),
        call("b", item_code="cola"),
        result("b"),
    ]

    assert shape(to_contents(messages)) == [
        ("user", ["text:Fries and a cola"]),
        ("model", ["call:add_item", "call:add_item"]),
        ("user", ["result:add_item", "result:add_item"]),
    ]


def test_calls_made_in_two_responses_stay_two_rounds() -> None:
    """Looked up, then added: the second call was decided after seeing the
    first result, and the history must say so."""
    messages: list[Message] = [
        UserMessage("Fries"),
        call("a", "get_item_details", response_id="resp-1", item_code="fries"),
        result("a", "get_item_details"),
        call("b", "add_item", response_id="resp-2", item_code="fries"),
        result("b", "add_item"),
    ]

    assert shape(to_contents(messages)) == [
        ("user", ["text:Fries"]),
        ("model", ["call:get_item_details"]),
        ("user", ["result:get_item_details"]),
        ("model", ["call:add_item"]),
        ("user", ["result:add_item"]),
    ]


def test_results_are_sent_in_the_order_the_calls_were_made() -> None:
    messages: list[Message] = [
        UserMessage("Go"),
        call("a", "search_menu"),
        call("b", "get_cart"),
        result("b", "get_cart"),
        result("a", "search_menu"),
    ]

    assert shape(to_contents(messages))[1:] == [
        ("model", ["call:search_menu", "call:get_cart"]),
        ("user", ["result:search_menu", "result:get_cart"]),
    ]


def test_the_thought_signature_goes_back_with_its_call_byte_for_byte() -> None:
    contents = to_contents(
        [UserMessage("Fries"), call("a", signature=SIGNATURE), result("a")]
    )

    part = contents[1].parts[0]  # pyright: ignore[reportOptionalSubscript]
    assert part.thought_signature == SIGNATURE


def test_a_call_with_no_signature_is_sent_with_none() -> None:
    contents = to_contents([UserMessage("Fries"), call("a"), result("a")])

    assert contents[1].parts[0].thought_signature is None  # pyright: ignore[reportOptionalSubscript]


def test_geminis_own_call_id_goes_back_on_the_call_and_the_result() -> None:
    contents = to_contents(
        [UserMessage("Fries"), call("fc_123", gemini_id="fc_123"), result("fc_123")]
    )

    assert contents[1].parts[0].function_call.id == "fc_123"  # pyright: ignore[reportOptionalSubscript, reportOptionalMemberAccess]
    assert contents[2].parts[0].function_response.id == "fc_123"  # pyright: ignore[reportOptionalSubscript, reportOptionalMemberAccess]


def test_an_id_we_made_up_is_not_passed_off_as_geminis() -> None:
    """Gemini gave the call no id. Ours pairs the result with the call inside
    the backend; it means nothing to Gemini and is not sent."""
    contents = to_contents(
        [UserMessage("Fries"), call("resp-1:0", gemini_id=None), result("resp-1:0")]
    )

    assert contents[1].parts[0].function_call.id is None  # pyright: ignore[reportOptionalSubscript, reportOptionalMemberAccess]
    assert contents[2].parts[0].function_response.id is None  # pyright: ignore[reportOptionalSubscript, reportOptionalMemberAccess]


def test_calls_recorded_by_another_provider_are_never_grouped() -> None:
    """With no record of which response a call came from, each is its own."""
    messages: list[Message] = [
        UserMessage("Fries and a cola"),
        call("a", response_id=None),
        result("a"),
        call("b", response_id=None),
        result("b"),
    ]

    assert shape(to_contents(messages))[1:] == [
        ("model", ["call:add_item"]),
        ("user", ["result:add_item"]),
        ("model", ["call:add_item"]),
        ("user", ["result:add_item"]),
    ]


def test_a_call_whose_result_is_missing_is_still_sent() -> None:
    contents = to_contents([UserMessage("Fries"), call("a")])

    assert shape(contents) == [("user", ["text:Fries"]), ("model", ["call:add_item"])]


def test_the_agent_speaking_twice_in_a_row_is_one_turn() -> None:
    """A reply, then the backend's own "Are you still there?"."""
    messages: list[Message] = [
        UserMessage("Hi"),
        AssistantMessage("Welcome!"),
        AssistantMessage("Are you still there?"),
        UserMessage("Yes"),
    ]

    assert shape(to_contents(messages)) == [
        ("user", ["text:Hi"]),
        ("model", ["text:Welcome!", "text:Are you still there?"]),
        ("user", ["text:Yes"]),
    ]


def test_text_is_not_folded_into_a_turn_of_tool_results() -> None:
    messages: list[Message] = [
        UserMessage("Fries"),
        call("a"),
        result("a"),
        UserMessage("And a cola"),
    ]

    assert shape(to_contents(messages))[2:] == [
        ("user", ["result:add_item"]),
        ("user", ["text:And a cola"]),
    ]


def test_a_conversation_the_backend_opened_starts_at_the_customers_first_words() -> (
    None
):
    """The silence nudge can come before the customer has said anything.
    Gemini wants the user to speak first."""
    messages: list[Message] = [
        AssistantMessage("Are you still there?"),
        UserMessage("Yes, a cookie please"),
    ]

    assert shape(to_contents(messages)) == [("user", ["text:Yes, a cookie please"])]


def test_an_empty_conversation_is_no_contents() -> None:
    assert to_contents([]) == []


# --- Tools ------------------------------------------------------------------


def test_every_tool_goes_in_one_tool_as_a_function_declaration() -> None:
    (tool,) = to_tools([TOOL, TOOL])

    declarations = tool.function_declarations or []
    assert [d.name for d in declarations] == ["remove_item", "remove_item"]
    assert declarations[0].description == "Remove a line from the order."


def test_the_schema_is_passed_through_as_json_schema_unchanged() -> None:
    (tool,) = to_tools([TOOL])

    (declaration,) = tool.function_declarations or []
    assert declaration.parameters_json_schema == TOOL.parameters
    assert declaration.parameters is None


def test_no_tools_is_no_tool_entry() -> None:
    assert to_tools([]) == []


def test_all_of_the_agents_real_tools_convert() -> None:
    (tool,) = to_tools(TOOL_DEFINITIONS)

    assert len(tool.function_declarations or []) == len(TOOL_DEFINITIONS)


# --- Gemini's response -> our turn ------------------------------------------


def function_call_part(
    name: str,
    args: dict[str, Any],
    *,
    call_id: str | None = None,
    signature: bytes | None = None,
) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(id=call_id, name=name, args=args),
        thought_signature=signature,
    )


def response(
    *parts: types.Part,
    usage: tuple[int, int, int] | None = (120, 15, 0),
    response_id: str | None = "resp-9",
    finish_reason: types.FinishReason = types.FinishReason.STOP,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0],
            candidates_token_count=usage[1],
            thoughts_token_count=usage[2],
        )
        if usage
        else None,
        model_version="gemini-test-001",
        response_id=response_id,
    )


def test_a_spoken_reply() -> None:
    turn = from_response(response(types.Part(text="Welcome!")))

    assert turn.text == "Welcome!"
    assert turn.tool_calls == ()
    assert turn.model == "gemini-test-001"
    assert turn.usage == ModelUsage(120, 15)


def test_text_in_several_parts_is_joined() -> None:
    turn = from_response(
        response(types.Part(text="Large fries. "), types.Part(text="Anything else?"))
    )

    assert turn.text == "Large fries. Anything else?"


def test_the_models_working_is_not_spoken() -> None:
    """Thought summaries are marked as thoughts. A customer must not hear them."""
    turn = from_response(
        response(
            types.Part(text="The user wants fries; I need the size.", thought=True),
            types.Part(text="What size fries?"),
        )
    )

    assert turn.text == "What size fries?"


def test_a_tool_call_keeps_geminis_id_and_arguments() -> None:
    turn = from_response(
        response(function_call_part("add_item", {"item_code": "fries"}, call_id="fc_1"))
    )

    (tool_call,) = turn.tool_calls
    assert (tool_call.call_id, tool_call.name) == ("fc_1", "add_item")
    assert tool_call.arguments == {"item_code": "fries"}
    assert turn.text is None


def test_a_tool_call_records_what_gemini_needs_back() -> None:
    turn = from_response(
        response(
            function_call_part("add_item", {}, call_id="fc_1", signature=SIGNATURE),
            response_id="resp-9",
        )
    )

    assert turn.tool_calls[0].provider_data == {
        "provider": "gemini",
        "response_id": "resp-9",
        "function_call_id": "fc_1",
        "thought_signature": base64.b64encode(SIGNATURE).decode(),
    }


def test_what_is_recorded_can_be_stored_as_json() -> None:
    """It goes into a JSON column, so a signature cannot stay as raw bytes."""
    turn = from_response(
        response(function_call_part("add_item", {}, signature=SIGNATURE))
    )

    assert json.loads(json.dumps(turn.tool_calls[0].provider_data))


def test_a_call_gemini_gave_no_id_gets_one_that_is_ours() -> None:
    turn = from_response(
        response(
            function_call_part("add_item", {"item_code": "fries"}),
            function_call_part("add_item", {"item_code": "cola"}),
            response_id="resp-9",
        )
    )

    first, second = turn.tool_calls
    assert (first.call_id, second.call_id) == ("resp-9:0", "resp-9:1")
    assert first.provider_data["function_call_id"] is None  # pyright: ignore[reportOptionalSubscript]


def test_made_up_ids_differ_between_responses_even_with_no_response_id() -> None:
    """The loop pairs results with calls by id, and runs each id once per
    turn. Two rounds must never produce the same one."""
    one = from_response(response(function_call_part("get_cart", {}), response_id=None))
    two = from_response(response(function_call_part("get_cart", {}), response_id=None))

    assert one.tool_calls[0].call_id != two.tool_calls[0].call_id
    assert (
        one.tool_calls[0].provider_data["response_id"]  # pyright: ignore[reportOptionalSubscript]
        != two.tool_calls[0].provider_data["response_id"]  # pyright: ignore[reportOptionalSubscript]
    )


def test_calls_of_one_response_share_a_response_id_so_they_regroup() -> None:
    turn = from_response(
        response(
            function_call_part("add_item", {"item_code": "fries"}, signature=SIGNATURE),
            function_call_part("add_item", {"item_code": "cola"}),
        )
    )
    a, b = turn.tool_calls
    history: list[Message] = [
        UserMessage("Fries and a cola"),
        a,
        ToolResult(a.call_id, a.name, {"decision": "allow"}),
        b,
        ToolResult(b.call_id, b.name, {"decision": "allow"}),
    ]

    contents = to_contents(history)

    assert shape(contents)[1:] == [
        ("model", ["call:add_item", "call:add_item"]),
        ("user", ["result:add_item", "result:add_item"]),
    ]
    # Round trip: what came out of one response goes back as it came.
    assert contents[1].parts[0].thought_signature == SIGNATURE  # pyright: ignore[reportOptionalSubscript]
    assert contents[1].parts[1].thought_signature is None  # pyright: ignore[reportOptionalSubscript]


def test_a_call_with_no_arguments_has_an_empty_mapping() -> None:
    part = types.Part(function_call=types.FunctionCall(name="get_cart", args=None))

    assert from_response(response(part)).tool_calls[0].arguments == {}


def test_thinking_is_counted_as_output_because_it_is_billed_as_output() -> None:
    turn = from_response(response(types.Part(text="Hi"), usage=(100, 20, 300)))

    assert turn.usage == ModelUsage(100, 320)


def test_usage_may_be_missing() -> None:
    assert from_response(response(types.Part(text="Hi"), usage=None)).usage is None


def test_the_configured_model_name_is_used_if_the_response_gives_none() -> None:
    bare = response(types.Part(text="Hi"))
    bare.model_version = None

    assert from_response(bare, fallback_model="gemini-2.5-flash-lite").model == (
        "gemini-2.5-flash-lite"
    )


@pytest.mark.parametrize(
    "empty",
    [
        types.GenerateContentResponse(candidates=[]),
        types.GenerateContentResponse(candidates=None),
        response(finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL),
        response(finish_reason=types.FinishReason.SAFETY),
    ],
)
def test_a_response_with_nothing_in_it_is_an_error_not_silence(
    empty: types.GenerateContentResponse,
) -> None:
    with pytest.raises(ModelUnavailable, match="returned nothing"):
        from_response(empty)


def test_the_error_for_an_empty_response_says_why() -> None:
    blocked = response(finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL)

    with pytest.raises(ModelUnavailable, match="MALFORMED_FUNCTION_CALL"):
        from_response(blocked)


# --- The request ------------------------------------------------------------


class StandInClient:
    """Shaped like genai.Client as far as the adapter uses it."""

    def __init__(self, answer: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self._answer = answer
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._send))

    async def _send(self, **request: Any) -> Any:
        self.requests.append(request)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def model_with(answer: Any) -> tuple[GeminiModel, StandInClient]:
    client = StandInClient(answer)
    model = GeminiModel(api_key="unused", model="gemini-2.5-flash-lite", client=client)  # pyright: ignore[reportArgumentType]
    return model, client


async def test_the_request_carries_the_model_the_instructions_and_the_tools() -> None:
    model, client = model_with(response(types.Part(text="Welcome!")))

    turn = await model.complete(
        instructions="You take orders.", messages=[UserMessage("Hi")], tools=[TOOL]
    )

    assert turn.text == "Welcome!"
    (request,) = client.requests
    assert request["model"] == "gemini-2.5-flash-lite"
    assert shape(request["contents"]) == [("user", ["text:Hi"])]
    config = request["config"]
    assert config.system_instruction == "You take orders."
    assert config.tools[0].function_declarations[0].name == "remove_item"


async def test_the_sdk_is_told_not_to_run_tools_itself() -> None:
    """Every tool call has to go through the policy gateway. If the SDK ran
    them, it would bypass it."""
    model, client = model_with(response(types.Part(text="Hi")))

    await model.complete(instructions="", messages=[UserMessage("Hi")], tools=[TOOL])

    assert client.requests[0]["config"].automatic_function_calling.disable is True


async def test_a_failed_request_is_reported_as_the_model_being_unavailable() -> None:
    failure = errors.ClientError(
        400,
        {
            "error": {
                "code": 400,
                "message": "API key not valid.",
                "status": "INVALID_ARGUMENT",
            }
        },
    )
    model, _ = model_with(failure)

    with pytest.raises(ModelUnavailable, match="API key not valid"):
        await model.complete(instructions="", messages=[UserMessage("Hi")], tools=[])


async def test_hitting_the_rate_limit_says_what_to_do() -> None:
    """The likeliest failure on the free tier, so it gets a useful message."""
    limited = errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded.",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )
    model, _ = model_with(limited)

    with pytest.raises(ModelUnavailable, match="rate limit was reached") as error:
        await model.complete(instructions="", messages=[UserMessage("Hi")], tools=[])

    assert "Quota exceeded." in str(error.value)


async def test_a_server_error_is_reported_too() -> None:
    down = errors.ServerError(
        503,
        {
            "error": {
                "code": 503,
                "message": "The model is overloaded.",
                "status": "UNAVAILABLE",
            }
        },
    )
    model, _ = model_with(down)

    with pytest.raises(ModelUnavailable, match="overloaded"):
        await model.complete(instructions="", messages=[UserMessage("Hi")], tools=[])
