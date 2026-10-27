"""What a policy check can conclude."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from drivetruagent.enums import SessionState
from drivetruagent.policy.caller_rules import CallerLimits, Standing


class Decision(StrEnum):
    # Fine: run the action.
    ALLOW = "allow"
    # Not possible: change nothing and say why.
    REJECT = "reject"
    # Unusual: change nothing yet, and check with the customer first.
    CONFIRM = "confirm"
    # A person should take over.
    ESCALATE = "escalate"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    # Which rule decided, as a stable code. None when every rule allowed it.
    rule: str | None = None
    # A short instruction for the agent: what to tell or ask the customer.
    message: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


ALLOWED = PolicyResult(Decision.ALLOW)


@dataclass(frozen=True)
class PolicyLimits:
    """The numbers behind the quantity rule. They come from settings."""

    # A line with more than this many of one item is unusual: ask first.
    line_quantity_confirm_above: int
    # A line with more than this many is extreme: a person handles it.
    line_quantity_escalate_above: int
    # So is a cart holding more than this many items in total.
    cart_items_escalate_above: int
    # What a phone number's history allows. See policy/caller_rules.py.
    callers: CallerLimits = field(default_factory=CallerLimits)


@dataclass(frozen=True)
class PolicyContext:
    """What the rules need to know about the session, and nothing more."""

    state: SessionState
    cart_item_count: int
    # Line number -> quantity, for the lines currently in the cart.
    line_quantities: Mapping[int, int]
    # What the cart costs now, everything included.
    cart_total_cents: int = 0
    # A phone order's caller standing; None for a drive-through car.
    caller_standing: Standing | None = None
