"""The policy rules. Pure logic, no database.

Limits used here: more than 10 on a line is unusual, more than 25 is extreme,
and more than 50 items in the cart is extreme.
"""

import pytest
from pydantic import ValidationError

from drivetruagent.config import Settings
from drivetruagent.enums import (
    ACTIVE_SESSION_STATES,
    FINAL_SESSION_STATES,
    SessionState,
)
from drivetruagent.policy.actions import (
    ACTION_ADAPTER,
    Action,
    AddItem,
    RemoveLine,
    ReplaceLine,
    RequestConfirmation,
    SubmitOrder,
    UpdateLine,
)
from drivetruagent.policy.decisions import (
    Decision,
    PolicyContext,
    PolicyLimits,
    PolicyResult,
)
from drivetruagent.policy.rules import evaluate

LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)

EVERY_ACTION: list[Action] = [
    AddItem(item_code="cookie"),
    UpdateLine(line_no=1, quantity=2),
    RemoveLine(line_no=1),
    ReplaceLine(line_no=1, item_code="fish_burger"),
    RequestConfirmation(),
    SubmitOrder(),
]


def context(
    state: SessionState = SessionState.ORDERING,
    lines: dict[int, int] | None = None,
) -> PolicyContext:
    lines = lines or {}
    return PolicyContext(
        state=state, cart_item_count=sum(lines.values()), line_quantities=lines
    )


def decide(
    action: Action,
    ctx: PolicyContext | None = None,
    *,
    confirmed: bool = False,
) -> PolicyResult:
    return evaluate(action, ctx or context(), LIMITS, confirmed=confirmed)


# --- State gating -----------------------------------------------------------


@pytest.mark.parametrize("state", sorted(FINAL_SESSION_STATES))
@pytest.mark.parametrize("action", EVERY_ACTION, ids=lambda a: a.type)
def test_an_ended_session_rejects_every_action(
    state: SessionState, action: Action
) -> None:
    result = decide(action, context(state))

    assert result.decision is Decision.REJECT
    assert result.rule == "session_ended"
    assert result.details == {"state": state.value}


@pytest.mark.parametrize("state", sorted(ACTIVE_SESSION_STATES))
@pytest.mark.parametrize(
    "action",
    [
        AddItem(item_code="cookie"),
        UpdateLine(line_no=1, quantity=2),
        RemoveLine(line_no=1),
    ],
    ids=lambda a: a.type,
)
def test_cart_changes_are_allowed_in_every_active_state(
    state: SessionState, action: Action
) -> None:
    assert decide(action, context(state, {1: 1})).decision is Decision.ALLOW


@pytest.mark.parametrize("state", [SessionState.GREETING, SessionState.ORDERING])
def test_submit_is_rejected_before_the_order_is_read_back(
    state: SessionState,
) -> None:
    result = decide(SubmitOrder(), context(state, {1: 1}))

    assert result.decision is Decision.REJECT
    assert result.rule == "order_not_confirmed"
    assert "request_confirmation" in result.message


def test_submit_is_allowed_once_the_order_is_being_confirmed() -> None:
    result = decide(SubmitOrder(), context(SessionState.CONFIRMING, {1: 1}))

    assert result.decision is Decision.ALLOW


@pytest.mark.parametrize("state", sorted(ACTIVE_SESSION_STATES))
def test_read_back_can_be_requested_in_any_active_state(state: SessionState) -> None:
    assert decide(RequestConfirmation(), context(state, {1: 1})).decision is (
        Decision.ALLOW
    )


# --- Quantity on a new line -------------------------------------------------


@pytest.mark.parametrize("quantity", [1, 2, 9, 10])
def test_normal_quantity_is_allowed(quantity: int) -> None:
    result = decide(AddItem(item_code="fries", quantity=quantity))

    assert result.decision is Decision.ALLOW
    assert result.rule is None


@pytest.mark.parametrize("quantity", [11, 15, 25])
def test_unusual_quantity_needs_confirmation(quantity: int) -> None:
    result = decide(AddItem(item_code="fries", quantity=quantity))

    assert result.decision is Decision.CONFIRM
    assert result.rule == "unusual_quantity"
    assert result.details["line_quantity"] == quantity
    assert str(quantity) in result.message


@pytest.mark.parametrize("quantity", [11, 15, 25])
def test_unusual_quantity_is_allowed_once_confirmed(quantity: int) -> None:
    result = decide(AddItem(item_code="fries", quantity=quantity), confirmed=True)

    assert result.decision is Decision.ALLOW


@pytest.mark.parametrize("quantity", [26, 100, 1000])
def test_extreme_quantity_is_still_asked_about_first(quantity: int) -> None:
    """A thousand fries is most likely a mishearing, so ask before handing off."""
    result = decide(AddItem(item_code="fries", quantity=quantity))

    assert result.decision is Decision.CONFIRM
    assert result.rule == "extreme_quantity"


@pytest.mark.parametrize("quantity", [26, 100, 1000])
def test_extreme_quantity_is_escalated_once_confirmed(quantity: int) -> None:
    result = decide(AddItem(item_code="fries", quantity=quantity), confirmed=True)

    assert result.decision is Decision.ESCALATE
    assert result.rule == "extreme_quantity"


@pytest.mark.parametrize("quantity", [0, -5])
def test_nonsense_quantity_is_left_to_the_ordering_rules(quantity: int) -> None:
    """Policy does not duplicate the "at least 1" check; ordering rejects it."""
    assert decide(AddItem(item_code="fries", quantity=quantity)).decision is (
        Decision.ALLOW
    )


# --- Cart size --------------------------------------------------------------


def test_cart_reaching_exactly_the_limit_is_allowed() -> None:
    ctx = context(lines={1: 10, 2: 10, 3: 10, 4: 10, 5: 5})  # 45 items

    assert decide(AddItem(item_code="cookie", quantity=5), ctx).decision is (
        Decision.ALLOW
    )


def test_cart_going_over_the_limit_needs_confirmation_then_escalates() -> None:
    ctx = context(lines={1: 10, 2: 10, 3: 10, 4: 10, 5: 5})  # 45 items
    action = AddItem(item_code="cookie", quantity=6)  # would make 51

    first = decide(action, ctx)
    after_yes = decide(action, ctx, confirmed=True)

    assert (first.decision, first.rule) == (Decision.CONFIRM, "extreme_quantity")
    assert after_yes.decision is Decision.ESCALATE
    assert after_yes.details == {"line_quantity": 6, "cart_item_count": 51}


# --- Quantity on an existing line -------------------------------------------


def test_update_is_judged_on_the_new_quantity() -> None:
    ctx = context(lines={1: 2})

    assert decide(UpdateLine(line_no=1, quantity=10), ctx).decision is Decision.ALLOW
    assert decide(UpdateLine(line_no=1, quantity=11), ctx).decision is Decision.CONFIRM


def test_update_counts_the_cart_after_the_change_not_before() -> None:
    """Line 1 goes from 10 to 6. The cart ends at 46, not 56."""
    ctx = context(lines={1: 10, 2: 10, 3: 10, 4: 10, 5: 10})  # 50 items

    assert decide(UpdateLine(line_no=1, quantity=6), ctx).decision is Decision.ALLOW


def test_reducing_an_unusual_line_is_allowed() -> None:
    ctx = context(lines={1: 20})

    assert decide(UpdateLine(line_no=1, quantity=3), ctx).decision is Decision.ALLOW


def test_changing_only_options_never_triggers_the_quantity_rule() -> None:
    ctx = context(lines={1: 20})
    action = UpdateLine(line_no=1, selections={"size": ["large"]})

    assert decide(action, ctx).decision is Decision.ALLOW


def test_update_of_a_line_that_does_not_exist_is_left_to_the_ordering_rules() -> None:
    assert decide(UpdateLine(line_no=9, quantity=500)).decision is Decision.ALLOW


def test_removing_a_line_never_triggers_the_quantity_rule() -> None:
    ctx = context(lines={1: 40, 2: 40})

    assert decide(RemoveLine(line_no=1), ctx).decision is Decision.ALLOW


def test_a_swap_keeps_the_lines_quantity_and_never_asks() -> None:
    """Swapping 2 cheeseburgers for fish burgers is 2 fish burgers."""
    ctx = context(lines={1: 2})

    assert decide(ReplaceLine(line_no=1, item_code="fish_burger"), ctx).decision is (
        Decision.ALLOW
    )


def test_a_swap_to_an_unusual_quantity_is_asked_about() -> None:
    ctx = context(lines={1: 2})
    action = ReplaceLine(line_no=1, item_code="fish_burger", quantity=12)

    assert decide(action, ctx).decision is Decision.CONFIRM


def test_a_swap_of_an_unusual_line_is_judged_like_the_line() -> None:
    """The line already had 12: swapping it is asked about, like adding 12."""
    ctx = context(lines={1: 12})

    assert decide(ReplaceLine(line_no=1, item_code="fish_burger"), ctx).decision is (
        Decision.CONFIRM
    )


def test_a_swap_of_a_missing_line_is_left_to_the_ordering_rules() -> None:
    action = ReplaceLine(line_no=9, item_code="fish_burger", quantity=500)

    assert decide(action).decision is Decision.ALLOW


# --- Rule order -------------------------------------------------------------


def test_state_is_checked_before_quantity() -> None:
    result = decide(
        AddItem(item_code="fries", quantity=1000), context(SessionState.SUBMITTED)
    )

    assert (result.decision, result.rule) == (Decision.REJECT, "session_ended")


def test_same_inputs_always_give_the_same_decision() -> None:
    action = AddItem(item_code="fries", quantity=15)

    assert decide(action) == decide(action)


# --- Actions ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "add_item", "item_code": "fries"}, AddItem(item_code="fries")),
        (
            {"type": "update_line", "line_no": 2, "quantity": 3},
            UpdateLine(line_no=2, quantity=3),
        ),
        ({"type": "remove_line", "line_no": 2}, RemoveLine(line_no=2)),
        (
            {"type": "replace_line", "line_no": 2, "item_code": "fish_burger"},
            ReplaceLine(line_no=2, item_code="fish_burger"),
        ),
        ({"type": "request_confirmation"}, RequestConfirmation()),
        ({"type": "submit_order"}, SubmitOrder()),
    ],
)
def test_action_is_chosen_by_its_type_tag(
    payload: dict[str, object], expected: Action
) -> None:
    assert ACTION_ADAPTER.validate_python(payload) == expected


def test_action_survives_a_round_trip_through_json() -> None:
    action = AddItem(item_code="fries", quantity=15, selections={"size": ["large"]})

    assert ACTION_ADAPTER.validate_python(action.model_dump(mode="json")) == action


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "give_discount", "percent": 100},
        {"type": "add_item", "item_code": "fries", "price_cents": 0},
        {"type": "add_item", "item_code": "fries", "unit_price_cents": 1},
        {"type": "submit_order", "total_cents": 0},
        {"type": "replace_line", "line_no": 1, "item_code": "x", "price_cents": 0},
        {"item_code": "fries"},
    ],
)
def test_an_action_cannot_carry_a_price_or_be_of_an_unknown_kind(
    payload: dict[str, object],
) -> None:
    """Money is never something a caller can propose."""
    with pytest.raises(ValidationError):
        ACTION_ADAPTER.validate_python(payload)


# --- Limits from settings ---------------------------------------------------


def settings(**overrides: int) -> Settings:
    values = {"database_url": "postgresql+psycopg://unused", **overrides}
    # _env_file=None keeps the developer's local .env out of the test.
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_default_limits() -> None:
    assert settings().policy_limits == LIMITS


def test_limits_can_be_changed_in_settings() -> None:
    limits = settings(
        line_quantity_confirm_above=5, line_quantity_escalate_above=12
    ).policy_limits

    assert limits.line_quantity_confirm_above == 5
    assert limits.line_quantity_escalate_above == 12


def test_escalation_limit_must_be_above_the_confirmation_limit() -> None:
    with pytest.raises(ValidationError, match="must be greater than"):
        settings(line_quantity_confirm_above=10, line_quantity_escalate_above=10)
