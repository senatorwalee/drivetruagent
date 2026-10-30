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


class UnknownPastOrder(OrderingError):
    code = "unknown_past_order"

    def __init__(self, order_number: int, recent: list[int]) -> None:
        super().__init__(
            f"Order {order_number} is not one of this caller's recent orders.",
            order_number=order_number,
            recent_order_numbers=recent,
        )


class InvalidPhoneNumber(OrderingError):
    code = "invalid_phone_number"

    def __init__(self) -> None:
        super().__init__("That is not a phone number that can be called back.")


class CallerNotFound(OrderingError):
    code = "caller_not_found"

    def __init__(self, caller_id: object) -> None:
        super().__init__(f"No caller with id {caller_id}.")


class TypedCallerIdOff(OrderingError):
    code = "typed_caller_id_off"

    def __init__(self) -> None:
        super().__init__(
            "A caller's number comes only from the phone line. Typing one in is "
            "for testing, and is off (ALLOW_TYPED_CALLER_ID)."
        )


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


class NotAPickupOrder(OrderingError):
    """Only a phone order is collected later, so only it has a pickup to mark."""

    code = "not_a_pickup_order"

    def __init__(self, order_id: object) -> None:
        super().__init__(
            f"Order {order_id} was not ordered by phone, so there is no pickup "
            "to mark.",
        )


class SessionInProgress(OrderingError):
    """A summary is written once a session has ended, not before."""

    code = "session_in_progress"

    def __init__(self, state: SessionState) -> None:
        super().__init__(
            f"This session is still going ({state.value}), so there is nothing "
            "to summarise yet.",
            state=state.value,
        )


class SummaryNotFound(OrderingError):
    code = "summary_not_found"

    def __init__(self, session_id: object) -> None:
        super().__init__(f"Session {session_id} has no summary yet.")


class NoPrices(OrderingError):
    """Costs are worked out from a price list, and there is none."""

    code = "no_prices"

    def __init__(self, path: object) -> None:
        super().__init__(
            f"No model prices could be read from {path}, so costs are not "
            "shown. Set MODEL_PRICES_PATH to a price list."
        )


class SessionNotHandedOff(OrderingError):
    """Only a session that was handed to a person can be finished by one."""

    code = "session_not_handed_off"

    def __init__(self, state: SessionState) -> None:
        super().__init__(
            f"This session is {state.value}, not handed off.", state=state.value
        )


class UnknownSpecial(OrderingError):
    code = "unknown_special"

    def __init__(self, special_code: str) -> None:
        super().__init__(
            f"There is no special with code '{special_code}'.",
            special_code=special_code,
        )


class InsufficientStock(OrderingError):
    """There are not enough of an item left for this session.

    `available` is what this session could have, after what other sessions
    are holding. The cart logic adds `alternatives`: items from the same
    category that are in stock, so the agent has something to offer.
    """

    code = "insufficient_stock"

    def __init__(self, item_code: str, requested: int, available: int) -> None:
        left = "none are left" if available == 0 else f"only {available} are left"
        super().__init__(
            f"{requested} of '{item_code}' were asked for, but {left}.",
            item_code=item_code,
            requested=requested,
            available=available,
        )


class IdempotencyKeyReused(OrderingError):
    code = "idempotency_key_reused"

    def __init__(self, key: str) -> None:
        super().__init__(
            f"The idempotency key '{key}' was already used for a different request.",
            key=key,
        )


class EmptyCart(OrderingError):
    code = "empty_cart"

    def __init__(self) -> None:
        super().__init__("The cart is empty, so there is nothing to submit.")


# --- Editing the menu -------------------------------------------------------


class UnknownCategory(OrderingError):
    code = "unknown_category"

    def __init__(self, category_code: str) -> None:
        super().__init__(
            f"There is no menu category with code '{category_code}'.",
            category_code=category_code,
        )


class DuplicateItemName(OrderingError):
    """Another item on the menu already has this English name. Two items the
    customer cannot tell apart by name would leave the agent guessing."""

    code = "duplicate_item_name"

    def __init__(self, name: str, other_item_code: str) -> None:
        super().__init__(
            f"'{other_item_code}' is already called '{name}'.",
            name=name,
            other_item_code=other_item_code,
        )


class DuplicateComboName(OrderingError):
    """Another combo already has this English name."""

    code = "duplicate_combo_name"

    def __init__(self, name: str) -> None:
        super().__init__(f"There is already a combo called '{name}'.", name=name)


class ComboNeedsItems(OrderingError):
    """A combo is made of one item from each part, so each part needs some."""

    code = "combo_needs_items"

    def __init__(self, combo: str, section: str) -> None:
        super().__init__(
            f"Combo '{combo}' takes an item from '{section}', which has none.",
            combo=combo,
            section=section,
        )


class ComboSavingTooLarge(OrderingError):
    """A saving as large as the cheapest set would make food free."""

    code = "combo_saving_too_large"

    def __init__(self, combo: str, saving_cents: int, cheapest_set_cents: int) -> None:
        super().__init__(
            f"Combo '{combo}' saves {saving_cents} cents, but its cheapest set "
            f"costs {cheapest_set_cents}.",
            combo=combo,
            saving_cents=saving_cents,
            cheapest_set_cents=cheapest_set_cents,
        )


class PriceBelowSpecial(OrderingError):
    """A price no larger than a special's discount would make the item free."""

    code = "price_below_special"

    def __init__(self, item_code: str, special_code: str, discount_cents: int) -> None:
        super().__init__(
            f"'{special_code}' takes {discount_cents} cents off '{item_code}', "
            "so its price must be higher than that.",
            item_code=item_code,
            special_code=special_code,
            discount_cents=discount_cents,
        )


class PhotoNotAccepted(OrderingError):
    """`reason`: empty, too_large or unsupported_type."""

    code = "photo_not_accepted"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, reason=reason)


class NoPhoto(OrderingError):
    code = "no_photo"

    def __init__(self, item_code: str) -> None:
        super().__init__(f"'{item_code}' has no photo.", item_code=item_code)
