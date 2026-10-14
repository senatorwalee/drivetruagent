"""The Gemini adapter: implements ModelClient with Google's generate_content.

All knowledge of Gemini's request and response format lives in this file. The
three conversion functions are pure, so they are tested without a network
call.

Requests are stateless: the whole conversation is sent each time, rebuilt
from the event log. Gemini attaches a few things to a function call that it
needs to see again (see `_provider_data`); those travel in the call's
`provider_data` and come back from the log with it.
"""

import base64
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from google import genai
from google.genai import errors, types

from drivetruagent.agent.messages import (
    Message,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import (
    ModelTurn,
    ModelUnavailable,
    ModelUsage,
    ToolDefinition,
)

PROVIDER = "gemini"
RATE_LIMITED = 429


class GeminiModel:
    def __init__(
        self, api_key: str, model: str, client: genai.Client | None = None
    ) -> None:
        self._model = model
        self._client = client or genai.Client(api_key=api_key)

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        config = types.GenerateContentConfig(
            system_instruction=instructions,
            tools=to_tools(tools),
            # The SDK can run tools by itself. Here the backend runs them,
            # through the policy gateway, so that is switched off.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=to_contents(messages),  # pyright: ignore[reportArgumentType]
                config=config,
            )
        except errors.APIError as error:
            raise ModelUnavailable(_describe(error)) from error
        return from_response(response, fallback_model=self._model)


# --- Our messages -> Gemini's contents --------------------------------------


def to_contents(messages: Sequence[Message]) -> list[types.Content]:
    """The conversation as Gemini contents.

    Gemini has two roles, `user` and `model`, and wants every call made in one
    model response in a single `model` turn, followed by all their results in
    a single `user` turn. The event log records call, result, call, result,
    so the calls are regrouped here by the response they came from.
    """
    contents: list[types.Content] = []
    round_: _ToolRound | None = None

    def close_round() -> None:
        nonlocal round_
        if round_ is not None:
            contents.extend(round_.contents())
            round_ = None

    for message in messages:
        if isinstance(message, ToolCall):
            if round_ is None or not round_.includes(message):
                close_round()
                round_ = _ToolRound(_response_id(message))
            round_.calls.append(message)
        elif isinstance(message, ToolResult):
            if round_ is not None:
                round_.results[message.call_id] = message
        else:
            close_round()
            role = "user" if isinstance(message, UserMessage) else "model"
            _add_text(contents, role, message.text)
    close_round()
    return _without_leading_model_turns(contents)


class _ToolRound:
    """The tool calls of one model response, and the results that came back."""

    def __init__(self, response_id: str | None) -> None:
        self.response_id = response_id
        self.calls: list[ToolCall] = []
        self.results: dict[str, ToolResult] = {}

    def includes(self, call: ToolCall) -> bool:
        """True if this call came from the same response as the others here.

        A call with no record of its response (made by another provider) is
        never grouped: it is treated as a response of its own.
        """
        return self.response_id is not None and _response_id(call) == self.response_id

    def contents(self) -> list[types.Content]:
        asked = types.Content(
            role="model", parts=[_call_part(call) for call in self.calls]
        )
        answers = [
            _result_part(call, self.results[call.call_id])
            for call in self.calls
            if call.call_id in self.results
        ]
        if not answers:
            return [asked]
        # Results go back in the order the calls were made.
        return [asked, types.Content(role="user", parts=answers)]


def _call_part(call: ToolCall) -> types.Part:
    data = call.provider_data or {}
    signature = data.get("thought_signature")
    return types.Part(
        function_call=types.FunctionCall(
            id=data.get("function_call_id"), name=call.name, args=call.arguments
        ),
        thought_signature=base64.b64decode(signature) if signature else None,
    )


def _result_part(call: ToolCall, result: ToolResult) -> types.Part:
    data = call.provider_data or {}
    return types.Part(
        function_response=types.FunctionResponse(
            id=data.get("function_call_id"), name=call.name, response=result.result
        )
    )


def _add_text(contents: list[types.Content], role: str, text: str) -> None:
    """Add spoken text, joining it to the turn before if the same side spoke.

    The agent can speak twice in a row (a reply, then "Are you still
    there?"). One turn with two parts reads the same and keeps the roles
    alternating.
    """
    part = types.Part(text=text)
    last = contents[-1] if contents else None
    if last is not None and last.role == role and _is_text_only(last):
        (last.parts or []).append(part)
    else:
        contents.append(types.Content(role=role, parts=[part]))


def _is_text_only(content: types.Content) -> bool:
    return all(part.text is not None for part in content.parts or [])


def _without_leading_model_turns(
    contents: list[types.Content],
) -> list[types.Content]:
    """A conversation has to open with the user. If the backend spoke first
    (a session can start with the silence nudge), that opening is left out."""
    first_user = next(
        (index for index, content in enumerate(contents) if content.role == "user"),
        len(contents),
    )
    return contents[first_user:]


def _response_id(call: ToolCall) -> str | None:
    return (call.provider_data or {}).get("response_id")


# --- Our tools -> Gemini's function declarations ----------------------------


def to_tools(tools: Sequence[ToolDefinition]) -> list[types.Tool]:
    """One Tool holding every function. The schemas are the same JSON Schemas
    OpenAI is given, passed through as they are."""
    if not tools:
        return []
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters,
                )
                for tool in tools
            ]
        )
    ]


# --- Gemini's response -> our turn ------------------------------------------


def from_response(
    response: types.GenerateContentResponse, *, fallback_model: str = ""
) -> ModelTurn:
    """What the model said and which tools it asked for.

    Raises ModelUnavailable if nothing came back. That happens when a reply
    is blocked or the model produced a call it could not finish; it is better
    reported than spoken as silence.
    """
    candidate = (response.candidates or [None])[0]
    parts = (candidate.content.parts if candidate and candidate.content else None) or []
    if not parts:
        reason = candidate.finish_reason if candidate else "no candidates"
        raise ModelUnavailable(f"The model returned nothing ({reason}).")

    # Thought summaries are the model's working, not something to say aloud.
    spoken = [part.text for part in parts if part.text and not part.thought]
    calls = [part for part in parts if part.function_call is not None]
    usage = response.usage_metadata
    return ModelTurn(
        text="".join(spoken) or None,
        tool_calls=tuple(
            _to_tool_call(part, index, response.response_id)
            for index, part in enumerate(calls)
        ),
        model=response.model_version or fallback_model,
        usage=ModelUsage(
            usage.prompt_token_count or 0,
            # Thinking is billed as output, so it is counted as output.
            (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
        )
        if usage
        else None,
    )


def _to_tool_call(part: types.Part, index: int, response_id: str | None) -> ToolCall:
    call = part.function_call
    assert call is not None
    # Gemini does not always give a call an id. The loop needs one that is
    # unique, to pair the result with the call and to run it only once.
    group = response_id or uuid.uuid4().hex
    return ToolCall(
        call_id=call.id or f"{group}:{index}",
        name=call.name or "",
        arguments=dict(call.args or {}),
        provider_data=_provider_data(part, response_id or group),
    )


def _provider_data(part: types.Part, response_id: str) -> Mapping[str, Any]:
    """What has to come back to Gemini with this call on the next request.

    - `response_id`: which response the call came from, so that calls made
      together are sent back together.
    - `function_call_id`: Gemini's own id for the call, if it gave one. Ours
      is always set; this records whether Gemini's was.
    - `thought_signature`: an encrypted note of the model's reasoning, as
      text. Newer models check that it is returned with the call.
    """
    call = part.function_call
    signature = part.thought_signature
    return {
        "provider": PROVIDER,
        "response_id": response_id,
        "function_call_id": call.id if call else None,
        "thought_signature": base64.b64encode(signature).decode("ascii")
        if signature
        else None,
    }


def _describe(error: errors.APIError) -> str:
    if error.code == RATE_LIMITED:
        return (
            "Gemini's rate limit was reached. On the free tier that is a few "
            f"requests a minute; wait and try again. ({error.message})"
        )
    return f"Gemini could not answer: {error.message or error}"
