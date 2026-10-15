"""What a person taking over a session is shown. Pure logic: no database.

The package is put together from rows that already exist. This module holds
its shape and the parts that are plain reasoning over values: turning the
event log into a short transcript and a list of steps, and saying in a
sentence or two what the person needs to do.

That sentence is fixed wording filled in from data. It is not written by a
model: someone acting on it should be reading what the backend knows.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from drivetruagent.enums import (
    AgentEventKind,
    HandoffOutcome,
    HandoffStatus,
    Language,
    ReservationStatus,
)
from drivetruagent.ordering.views import CartSnapshot, OrderView
from drivetruagent.policy.rules import (
    ALLERGEN_UNVERIFIED,
    EXTREME_QUANTITY,
    HUMAN_REQUESTED,
    REPEATED_CONFUSION,
    TIME_LIMIT,
)

# How much of a conversation a person can take in at a glance.
TRANSCRIPT_LINES = 12
STEPS = 10

_TITLES = {
    EXTREME_QUANTITY: "Very large order",
    HUMAN_REQUESTED: "A person was asked for",
    TIME_LIMIT: "Took longer than the time limit",
    REPEATED_CONFUSION: "Kept going off track",
    ALLERGEN_UNVERIFIED: "Allergen question with no checked answer",
}


@dataclass(frozen=True)
class LoggedEvent:
    """One row of a session's event log, as plain values."""

    kind: AgentEventKind
    payload: Mapping[str, Any]
    at: datetime


class TranscriptLine(BaseModel):
    speaker: Literal["customer", "agent"]
    text: str
    at: datetime


class Step(BaseModel):
    """One thing the agent asked the backend to do, or a timer firing."""

    at: datetime
    # The tool's name, or "timer".
    tool: str
    arguments: dict[str, Any]
    # allow, reject, confirm or escalate. None for a lookup or a timer.
    decision: str | None
    # The rule or error code behind the decision, if there was one.
    rule: str | None
    # What the backend told the agent to do next.
    message: str | None


class HeldStock(BaseModel):
    item_code: str
    item_name: str
    quantity: int
    status: ReservationStatus
    expires_at: datetime
    # True while the hold still keeps these units from other customers.
    held: bool


class HandoffReason(BaseModel):
    code: str
    title: str


class HandoffSummary(BaseModel):
    """One row of the queue."""

    handoff_id: uuid.UUID
    session_id: uuid.UUID
    lane: str
    language: Language
    status: HandoffStatus
    reason: HandoffReason
    created_at: datetime
    acknowledged_by: str | None
    item_count: int
    total_cents: int


class HandoffPackage(HandoffSummary):
    """Everything the person taking over needs, so the customer is not asked
    to start again."""

    # What the rule knew when it decided.
    details: dict[str, Any]
    # What the person needs to settle, in a sentence or two.
    outstanding: str
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    outcome: HandoffOutcome | None
    note: str | None
    session_started_at: datetime
    session_ended_at: datetime | None
    cart: CartSnapshot
    stock: list[HeldStock]
    # The most recent things said, oldest first.
    transcript: list[TranscriptLine]
    # How many earlier lines were left out.
    earlier_lines: int
    steps: list[Step]
    earlier_steps: int
    # Present once the handoff was resolved by placing the order.
    order: OrderView | None


def reason_title(code: str) -> str:
    """A rule's code as a heading a person can read."""
    return _TITLES.get(code, code.replace("_", " ").capitalize())


def transcript_of(
    events: Sequence[LoggedEvent], limit: int = TRANSCRIPT_LINES
) -> tuple[list[TranscriptLine], int]:
    """The last `limit` things said, and how many came before them."""
    speakers: dict[AgentEventKind, Literal["customer", "agent"]] = {
        AgentEventKind.USER_MESSAGE: "customer",
        AgentEventKind.ASSISTANT_MESSAGE: "agent",
    }
    lines = [
        TranscriptLine(
            speaker=speakers[event.kind],
            text=str(event.payload.get("text", "")),
            at=event.at,
        )
        for event in events
        if event.kind in speakers
    ]
    return _last(lines, limit)


def steps_of(
    events: Sequence[LoggedEvent], limit: int = STEPS
) -> tuple[list[Step], int]:
    """The last `limit` tool calls and timer events, and how many came before.

    A tool call is listed once its result is known, with the backend's
    decision. A call with no result yet (the turn is still running) is left
    out: there is nothing to say about it.
    """
    arguments: dict[str, dict[str, Any]] = {}
    steps: list[Step] = []
    for event in events:
        payload = event.payload
        if event.kind is AgentEventKind.TOOL_CALL:
            arguments[str(payload.get("call_id"))] = dict(payload.get("arguments", {}))
        elif event.kind is AgentEventKind.TOOL_RESULT:
            result = payload.get("result", {})
            error = result.get("error") or {}
            steps.append(
                Step(
                    at=event.at,
                    tool=str(payload.get("name")),
                    arguments=arguments.get(str(payload.get("call_id")), {}),
                    decision=result.get("decision"),
                    rule=result.get("rule") or error.get("code"),
                    message=result.get("message") or error.get("message"),
                )
            )
        elif event.kind is AgentEventKind.TIMER:
            steps.append(
                Step(
                    at=event.at,
                    tool="timer",
                    arguments={"after_seconds": payload.get("after_seconds")},
                    decision=None,
                    rule=payload.get("rule"),
                    message=str(payload.get("action", "")).replace("_", " "),
                )
            )
    return _last(steps, limit)


def outstanding(
    reason: str,
    details: Mapping[str, Any],
    action: Mapping[str, Any] | None,
    *,
    item_name: str | None,
    cart_item_count: int,
) -> str:
    """What the person taking over needs to settle.

    `item_name` is the display name of the item the triggering action was
    about, if there was one.
    """
    return (
        f"{_what_happened(reason, details, action, item_name)} {_cart(cart_item_count)}"
    )


def _what_happened(
    reason: str,
    details: Mapping[str, Any],
    action: Mapping[str, Any] | None,
    item_name: str | None,
) -> str:
    if reason == EXTREME_QUANTITY:
        quantity = (action or {}).get("quantity", details.get("line_quantity"))
        if action and action.get("type") == "update_line":
            asked = f"to change line L{action.get('line_no')} to {quantity}"
        else:
            asked = f"for {quantity} of {item_name or 'an item'}"
        return (
            f"The customer asked {asked} and confirmed it. That is more than the "
            "agent may take, so it was not added. Check the quantity with them."
        )
    if reason == HUMAN_REQUESTED:
        why = str(details.get("reason", "")).strip()
        given = f' The reason given: "{why}".' if why else ""
        return (
            f"A person was asked for.{given} Carry on from where the "
            "conversation stopped."
        )
    if reason == REPEATED_CONFUSION:
        return (
            "The conversation kept going off track: off-topic questions, or the "
            "agent not understanding. Ask what the customer would like."
        )
    if reason == ALLERGEN_UNVERIFIED:
        items = _listed(details.get("items"))
        allergens = _listed(details.get("allergens"))
        about = f" ({allergens.replace('_', ' ')})" if allergens else ""
        in_items = f" in {items}" if items else ""
        question = str(details.get("question", "")).strip()
        asked = f' They asked: "{question}".' if question else ""
        return (
            f"The customer asked about allergens{about}{in_items}, and there is "
            f"no checked information to answer it.{asked} Answer from the "
            "allergen binder or ask the manager before they order."
        )
    if reason == TIME_LIMIT:
        return (
            "The order was not finished within the time limit. Finish it with "
            "the customer."
        )
    return (
        f"Handed off by the rule '{reason}'. Read the conversation and carry on "
        "from there."
    )


def _listed(values: object) -> str:
    """A list from a handoff's details as words: "a, b and c"."""
    if not isinstance(values, list):
        return ""
    words = [str(value) for value in values if str(value).strip()]
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _cart(item_count: int) -> str:
    if item_count == 0:
        return "The cart is empty."
    items = "1 item" if item_count == 1 else f"{item_count} items"
    return f"The cart holds {items} and can be placed as it stands."


def _last[T](values: list[T], limit: int) -> tuple[list[T], int]:
    earlier = max(0, len(values) - limit)
    return values[earlier:], earlier
