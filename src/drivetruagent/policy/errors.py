"""What can go wrong in the policy layer itself.

These share OrderingError as their base, so the API answers them in the same
JSON shape as every other expected failure.
"""

from drivetruagent.ordering.errors import OrderingError


class PendingActionNotFound(OrderingError):
    code = "pending_action_not_found"

    def __init__(self, pending_action_id: object) -> None:
        super().__init__(f"This session has no pending action {pending_action_id}.")


class HandoffNotFound(OrderingError):
    code = "handoff_not_found"

    def __init__(self, handoff_id: object) -> None:
        super().__init__(f"No handoff with id {handoff_id}.")


class HandoffResolved(OrderingError):
    """The handoff has been finished. It cannot be changed again."""

    code = "handoff_resolved"

    def __init__(self, outcome: str) -> None:
        super().__init__(
            f"This handoff has already been resolved ({outcome}).", outcome=outcome
        )
