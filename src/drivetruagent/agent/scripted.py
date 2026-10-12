"""A stand-in model that replays a script.

Used by tests to drive the agent loop deterministically, with no network and
no cost. It also records what it was asked, so a test can check exactly what
the loop sent to the model.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from drivetruagent.agent.messages import Message, ToolCall
from drivetruagent.agent.model import ModelTurn, ToolDefinition


@dataclass(frozen=True)
class ModelRequest:
    instructions: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]


@dataclass
class ScriptedModel:
    """Returns the given turns in order, one per call."""

    turns: list[ModelTurn]
    requests: list[ModelRequest] = field(default_factory=list)

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        self.requests.append(ModelRequest(instructions, tuple(messages), tuple(tools)))
        if not self.turns:
            raise AssertionError(
                "The scripted model was asked for more turns than scripted."
            )
        return self.turns.pop(0)


def say(text: str) -> ModelTurn:
    """A scripted turn where the model speaks to the customer."""
    return ModelTurn(text=text, model="scripted")


def call(name: str, call_id: str = "call_1", **arguments: object) -> ModelTurn:
    """A scripted turn where the model asks for one tool."""
    return ModelTurn(
        tool_calls=(ToolCall(call_id=call_id, name=name, arguments=dict(arguments)),),
        model="scripted",
    )


def calls(*tool_calls: ToolCall) -> ModelTurn:
    """A scripted turn where the model asks for several tools at once."""
    return ModelTurn(tool_calls=tool_calls, model="scripted")
