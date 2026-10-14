"""The rules. Pure logic: no database, no HTTP, no model.

`evaluate` takes a proposed action and what is known about the session, and
returns a decision. The same inputs always give the same decision.
"""

from drivetruagent.enums import FINAL_SESSION_STATES, SessionState
from drivetruagent.policy.actions import (
    Action,
    AddItem,
    SubmitOrder,
    UpdateLine,
)
from drivetruagent.policy.decisions import (
    ALLOWED,
    Decision,
    PolicyContext,
    PolicyLimits,
    PolicyResult,
)

SESSION_ENDED = "session_ended"
ORDER_NOT_CONFIRMED = "order_not_confirmed"
UNUSUAL_QUANTITY = "unusual_quantity"
EXTREME_QUANTITY = "extreme_quantity"
# Reasons a session ends that are not decided by the rules below, but are
# named here with the rest so every reason code lives in one place.
HUMAN_REQUESTED = "human_requested"
TIME_LIMIT = "time_limit"
SILENCE_TIMEOUT = "silence_timeout"
# The guardrail's two outcomes: three strikes for abuse, or for confusion.
ABUSE = "abuse"
REPEATED_CONFUSION = "repeated_confusion"
# An allergen question about an item with no reviewed allergen information.
ALLERGEN_UNVERIFIED = "allergen_unverified"


def evaluate(
    action: Action,
    context: PolicyContext,
    limits: PolicyLimits,
    *,
    confirmed: bool = False,
) -> PolicyResult:
    """Decide what should happen to a proposed action.

    `confirmed` is True only when the customer has already been asked about
    this exact action and said yes.
    """
    for rule in (_check_state, _check_quantity):
        result = rule(action, context, limits, confirmed)
        if result.decision is not Decision.ALLOW:
            return result
    return ALLOWED


def _check_state(
    action: Action, context: PolicyContext, limits: PolicyLimits, confirmed: bool
) -> PolicyResult:
    """Which actions the session's state allows."""
    if context.state in FINAL_SESSION_STATES:
        return PolicyResult(
            Decision.REJECT,
            SESSION_ENDED,
            "This session has ended. Nothing more can be changed.",
            {"state": context.state.value},
        )
    if isinstance(action, SubmitOrder) and context.state is not SessionState.CONFIRMING:
        return PolicyResult(
            Decision.REJECT,
            ORDER_NOT_CONFIRMED,
            "Read the order back and ask the customer to confirm it before "
            "submitting. Use request_confirmation first.",
            {"state": context.state.value},
        )
    return ALLOWED


def _check_quantity(
    action: Action, context: PolicyContext, limits: PolicyLimits, confirmed: bool
) -> PolicyResult:
    """The "1,000 fries" rule, judged on what the cart would hold afterwards."""
    resulting = _resulting_quantities(action, context)
    if resulting is None:
        return ALLOWED
    line_quantity, cart_items = resulting

    extreme = (
        line_quantity > limits.line_quantity_escalate_above
        or cart_items > limits.cart_items_escalate_above
    )
    unusual = line_quantity > limits.line_quantity_confirm_above
    if not extreme and not unusual:
        return ALLOWED

    details = {"line_quantity": line_quantity, "cart_item_count": cart_items}
    if not confirmed:
        # Mishearing is the likeliest cause, so the customer is always asked
        # first, however large the number.
        return PolicyResult(
            Decision.CONFIRM,
            EXTREME_QUANTITY if extreme else UNUSUAL_QUANTITY,
            f"That would be {line_quantity} of one item. Repeat the quantity "
            "back to the customer and ask if it is right.",
            details,
        )
    if extreme:
        return PolicyResult(
            Decision.ESCALATE,
            EXTREME_QUANTITY,
            "This order is too large to take here. Tell the customer a team "
            "member will help them.",
            details,
        )
    return ALLOWED


def _resulting_quantities(
    action: Action, context: PolicyContext
) -> tuple[int, int] | None:
    """(the line's quantity, items in the cart) after the action, or None if
    the action does not change a quantity."""
    if isinstance(action, AddItem):
        return action.quantity, context.cart_item_count + action.quantity
    if isinstance(action, UpdateLine) and action.quantity is not None:
        current = context.line_quantities.get(action.line_no)
        if current is None:
            # No such line. The ordering rules will refuse it with a clear error.
            return None
        return action.quantity, context.cart_item_count - current + action.quantity
    return None
