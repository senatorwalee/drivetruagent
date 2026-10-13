"""What the agent loop needs from a model, and nothing more.

The loop depends on this small interface, not on any provider's library. The
OpenAI adapter implements it for real conversations and the scripted model
implements it for tests.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from drivetruagent.agent.messages import Message, ToolCall


@dataclass(frozen=True)
class ToolDefinition:
    """A tool as the model sees it: a name, when to use it, and its arguments."""

    name: str
    description: str
    # A JSON Schema object describing the arguments.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ModelTurn:
    """One response from the model: words for the customer, tool calls, or both."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    model: str = ""
    usage: ModelUsage | None = None


class ModelClient(Protocol):
    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        """Ask the model for its next step, given the conversation so far."""
        ...


class ModelUnavailable(Exception):
    """The model could not be reached or is not configured."""
