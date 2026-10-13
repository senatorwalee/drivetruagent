"""The one result shape every proposed action returns.

Whatever was decided, the caller gets the decision, a short instruction, and
the current cart. The agent never has to guess what state things are in.
"""

import uuid
from typing import Any

from pydantic import BaseModel

from drivetruagent.ordering.views import CartSnapshot, OrderView, SuggestionView
from drivetruagent.policy.decisions import Decision


class OutcomeError(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class ActionOutcome(BaseModel):
    decision: Decision
    # The rule or error code behind the decision. None for a plain allow.
    rule: str | None = None
    # A short instruction for the agent: what to tell or ask the customer.
    message: str
    # The cart as it is now, after the decision took effect (or did not).
    cart: CartSnapshot
    # Present after a successful submit_order.
    order: OrderView | None = None
    # Present on `confirm`. Pass it back once the customer has answered.
    pending_action_id: uuid.UUID | None = None
    # Present on `reject`: why, in a form code can act on.
    error: OutcomeError | None = None
    # Present when there is something worth offering the customer, once.
    suggestion: SuggestionView | None = None
