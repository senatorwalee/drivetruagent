"""What can go wrong when ordering.

These are expected outcomes, not bugs: the customer asked for something that
is not on the menu, or left out a required choice. Each error carries a
stable `code` and structured `details`, so a caller can react (ask a question,
offer alternatives) without parsing the message text.
"""

from typing import Any

from drivetruagent.enums import SessionState


class OrderingError(Exception):
    """Base class. Subclasses set `code`; instances carry `details`."""

    code: str = "ordering_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class SessionNotFound(OrderingError):
    code = "session_not_found"

    def __init__(self, session_id: object) -> None:
        super().__init__(f"No session with id {session_id}.")


class SessionEnded(OrderingError):
    code = "session_ended"

    def __init__(self, state: SessionState) -> None:
        super().__init__(
            f"The session has ended ({state.value}) and can no longer change.",
            state=state.value,
        )


class LaneBusy(OrderingError):
    code = "lane_busy"

    def __init__(self, lane: str) -> None:
        super().__init__(f"Lane {lane} already has an active session.", lane=lane)


class UnknownItem(OrderingError):
    code = "unknown_item"

    def __init__(self, item_code: str) -> None:
        super().__init__(
            f"There is no menu item with code '{item_code}'.", item_code=item_code
        )


class ItemUnavailable(OrderingError):
    code = "item_unavailable"

    def __init__(self, item_code: str) -> None:
        super().__init__(
            f"'{item_code}' is not available right now.", item_code=item_code
        )


class UnknownOptionGroup(OrderingError):
    code = "unknown_option_group"

    def __init__(self, item_code: str, group_code: str, valid: list[str]) -> None:
        super().__init__(
            f"'{item_code}' has no choice called '{group_code}'.",
            item_code=item_code,
            group_code=group_code,
            valid_groups=valid,
        )


class UnknownOption(OrderingError):
    code = "unknown_option"

    def __init__(self, group_code: str, option_code: str, valid: list[str]) -> None:
        super().__init__(
            f"'{option_code}' is not a valid {group_code}.",
            group_code=group_code,
            option_code=option_code,
            valid_options=valid,
        )


class OptionUnavailable(OrderingError):
    code = "option_unavailable"

    def __init__(self, group_code: str, option_code: str, valid: list[str]) -> None:
        super().__init__(
            f"{group_code} '{option_code}' is not available right now.",
            group_code=group_code,
            option_code=option_code,
            valid_options=valid,
        )


class MissingRequiredOptions(OrderingError):
    """The customer has not yet made a choice the item needs.

    `details["missing"]` lists every such group at once, each with the options
    that may be offered, so the agent can ask without another lookup.
    """

    code = "missing_required_options"

    def __init__(self, item_code: str, missing: list[dict[str, Any]]) -> None:
        names = ", ".join(group["group_code"] for group in missing)
        super().__init__(
            f"'{item_code}' still needs a choice for: {names}.",
            item_code=item_code,
            missing=missing,
        )


class TooManyOptions(OrderingError):
    code = "too_many_options"

    def __init__(self, group_code: str, max_select: int, chosen: list[str]) -> None:
        super().__init__(
            f"At most {max_select} may be chosen for {group_code}; "
            f"{len(chosen)} were given.",
            group_code=group_code,
            max_select=max_select,
            chosen=chosen,
        )


class TooFewOptions(OrderingError):
    code = "too_few_options"

    def __init__(self, group_code: str, min_select: int, chosen: list[str]) -> None:
        super().__init__(
            f"At least {min_select} must be chosen for {group_code}; "
            f"{len(chosen)} were given.",
            group_code=group_code,
            min_select=min_select,
            chosen=chosen,
        )


class InvalidQuantity(OrderingError):
    code = "invalid_quantity"

    def __init__(self, quantity: int) -> None:
        super().__init__(
            f"Quantity must be at least 1; {quantity} was given.", quantity=quantity
        )


class UnknownCartLine(OrderingError):
    code = "unknown_cart_line"

    def __init__(self, line_no: int, existing: list[int]) -> None:
        super().__init__(
            f"The cart has no line {line_no}.", line_no=line_no, existing_lines=existing
        )


class NothingToUpdate(OrderingError):
    code = "nothing_to_update"

    def __init__(self) -> None:
        super().__init__("Give a new quantity, new options, or both.")


class OrderNotFound(OrderingError):
    code = "order_not_found"

    def __init__(self, order_id: object) -> None:
        super().__init__(f"No order with id {order_id}.")


class PendingActionNotFound(OrderingError):
    code = "pending_action_not_found"

    def __init__(self, pending_action_id: object) -> None:
        super().__init__(f"This session has no pending action {pending_action_id}.")


class EmptyCart(OrderingError):
    code = "empty_cart"

    def __init__(self) -> None:
        super().__init__("The cart is empty, so there is nothing to submit.")
