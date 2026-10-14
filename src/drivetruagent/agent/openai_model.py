"""The OpenAI adapter: implements ModelClient with the Responses API.

All knowledge of OpenAI's request and response format lives in this file. The
two conversion functions are pure, so they are tested without a network call.

Requests are stateless: the whole conversation is sent each time, and OpenAI
is asked not to store it (`store=False`). The event log is the only record.
"""

import json
from collections.abc import Sequence
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.responses import Response

from drivetruagent.agent.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    UserMessage,
)
from drivetruagent.agent.model import (
    ModelTurn,
    ModelUnavailable,
    ModelUsage,
    ToolDefinition,
)
from drivetruagent.knowledge.embedding import DIMENSIONS, EmbeddingUnavailable

# Kept under a key the argument checks will reject, so a tool call whose
# arguments were not valid JSON comes back to the model as invalid_tool_call.
UNPARSEABLE_ARGUMENTS = "_unparseable_arguments"


class OpenAIModel:
    def __init__(
        self, api_key: str, model: str, client: AsyncOpenAI | None = None
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key)

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        try:
            response = await self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=to_input_items(messages),  # pyright: ignore[reportArgumentType]
                tools=to_tool_params(tools),  # pyright: ignore[reportArgumentType]
                store=False,
            )
        except openai.OpenAIError as error:
            raise ModelUnavailable(str(error)) from error
        return from_response(response)


class OpenAIEmbedder:
    """Implements knowledge.embedding.Embedder with OpenAI's embeddings API,
    asking for DIMENSIONS-long vectors (text-embedding-3 models can shorten
    theirs)."""

    # Measured with text-embedding-3-small at 384 dimensions on the labelled
    # questions in evals/retrieval.py: the passage that answers scored 0.50
    # to 0.78 in English and 0.38 for a French question; off-topic questions
    # scored at most 0.20. The retrieval evals check this still holds.
    min_score = 0.35

    def __init__(
        self, api_key: str, model: str, client: AsyncOpenAI | None = None
    ) -> None:
        self.model = model
        self._client = client or AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(
                model=self.model, input=list(texts), dimensions=DIMENSIONS
            )
        except openai.OpenAIError as error:
            raise EmbeddingUnavailable(str(error)) from error
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def to_input_items(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """The conversation as Responses API input items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            items.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            items.append({"role": "assistant", "content": message.text})
        elif isinstance(message, ToolCall):
            items.append(
                {
                    "type": "function_call",
                    "call_id": message.call_id,
                    "name": message.name,
                    # The API carries arguments as a JSON string.
                    "arguments": json.dumps(message.arguments, ensure_ascii=False),
                }
            )
        else:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.call_id,
                    "output": json.dumps(message.result, ensure_ascii=False),
                }
            )
    return items


def to_tool_params(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            # The model's arguments must match the schema exactly.
            "strict": True,
        }
        for tool in tools
    ]


def from_response(response: Response) -> ModelTurn:
    tool_calls = tuple(
        ToolCall(
            call_id=item.call_id,
            name=item.name,
            arguments=_parse_arguments(item.arguments),
        )
        for item in response.output
        if item.type == "function_call"
    )
    usage = response.usage
    return ModelTurn(
        text=response.output_text or None,
        tool_calls=tool_calls,
        model=response.model,
        usage=ModelUsage(usage.input_tokens, usage.output_tokens) if usage else None,
    )


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {UNPARSEABLE_ARGUMENTS: raw}
    return parsed if isinstance(parsed, dict) else {UNPARSEABLE_ARGUMENTS: raw}
