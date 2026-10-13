"""What can go wrong in the policy layer itself.

These share OrderingError as their base, so the API answers them in the same
JSON shape as every other expected failure.
"""

from drivetruagent.ordering.errors import OrderingError


class PendingActionNotFound(OrderingError):
    code = "pending_action_not_found"

    def __init__(self, pending_action_id: object) -> None:
        super().__init__(f"This session has no pending action {pending_action_id}.")
