"""A conversation in a form that belongs to no model provider.

The event log stores these, the loop passes them around, and each model
adapter converts them to its provider's own format at the last moment.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """The model asking for a tool to be run."""

    # Chosen by the model; ties a result back to the call it answers.
    call_id: str
    name: str
    arguments: dict[str, Any]
    # Anything the provider attached to this call and needs to be given back
    # unchanged on the next request (Gemini's thought signature, for one). The
    # adapter that made it is the only code that looks inside. It is stored
    # with the call, because the event log is the only conversation store.
    provider_data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ToolResult:
    """What a tool returned, to be shown to the model."""

    call_id: str
    name: str
    result: dict[str, Any]


type Message = UserMessage | AssistantMessage | ToolCall | ToolResult
