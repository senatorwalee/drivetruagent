"""What a person taking over is shown, worked out from plain values.

No database: events are built by hand.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from drivetruagent.enums import AgentEventKind
from drivetruagent.policy.handoff_package import (
    LoggedEvent,
    outstanding,
    reason_title,
    steps_of,
    transcript_of,
)

START = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


class Log:
    """Builds an event log, one second apart."""

    def __init__(self) -> None:
        self.events: list[LoggedEvent] = []

    def _add(self, kind: AgentEventKind, payload: dict[str, Any]) -> "Log":
        at = START + timedelta(seconds=len(self.events))
        self.events.append(LoggedEvent(kind, payload, at))
        return self

    def customer(self, text: str) -> "Log":
        return self._add(AgentEventKind.USER_MESSAGE, {"text": text})

    def agent(self, text: str) -> "Log":
        return self._add(AgentEventKind.ASSISTANT_MESSAGE, {"text": text})

    def model_call(self) -> "Log":
        return self._add(AgentEventKind.MODEL_CALL, {"model": "scripted"})

    def call(self, call_id: str, name: str, **arguments: Any) -> "Log":
        payload = {"call_id": call_id, "name": name, "arguments": arguments}
        return self._add(AgentEventKind.TOOL_CALL, payload)

    def result(self, call_id: str, name: str, **result: Any) -> "Log":
        payload = {"call_id": call_id, "name": name, "result": result}
        return self._add(AgentEventKind.TOOL_RESULT, payload)

    def timer(self, action: str, rule: str, after_seconds: int) -> "Log":
        payload = {"action": action, "rule": rule, "after_seconds": after_seconds}
        return self._add(AgentEventKind.TIMER, payload)


# --- Transcript -------------------------------------------------------------


def test_the_transcript_is_what_was_said_in_order_with_who_said_it() -> None:
    log = Log().customer("Hi").agent("Welcome!").customer("A cookie")

    lines, earlier = transcript_of(log.events)

    assert [(line.speaker, line.text) for line in lines] == [
        ("customer", "Hi"),
        ("agent", "Welcome!"),
        ("customer", "A cookie"),
    ]
    assert earlier == 0
    assert lines[1].at == START + timedelta(seconds=1)


def test_only_speech_is_in_the_transcript() -> None:
    log = (
        Log()
        .customer("A cookie")
        .model_call()
        .call("c1", "add_item", item_code="cookie")
        .result("c1", "add_item", decision="allow")
        .timer("nudge", "silence", 20)
        .agent("One cookie.")
    )

    lines, _ = transcript_of(log.events)

    assert [line.text for line in lines] == ["A cookie", "One cookie."]


def test_a_long_conversation_keeps_its_most_recent_lines() -> None:
    log = Log()
    for number in range(1, 21):
        log.customer(f"line {number}")

    lines, earlier = transcript_of(log.events)

    assert [line.text for line in lines] == [f"line {n}" for n in range(9, 21)]
    assert earlier == 8


def test_an_empty_log_has_an_empty_transcript() -> None:
    assert transcript_of([]) == ([], 0)


# --- Steps ------------------------------------------------------------------


def test_a_step_pairs_a_call_with_its_result() -> None:
    log = (
        Log()
        .call("c1", "add_item", item_code="fries", quantity=1)
        .result(
            "c1",
            "add_item",
            decision="allow",
            message="Done. Briefly tell the customer what changed.",
        )
    )

    (step,), earlier = steps_of(log.events)

    assert (step.tool, step.decision, step.rule) == ("add_item", "allow", None)
    assert step.arguments == {"item_code": "fries", "quantity": 1}
    assert step.message == "Done. Briefly tell the customer what changed."
    # The time of the result: that is when the decision was made.
    assert step.at == START + timedelta(seconds=1)
    assert earlier == 0


def test_a_rejection_shows_its_rule() -> None:
    log = (
        Log()
        .call("c1", "submit_order")
        .result(
            "c1",
            "submit_order",
            decision="reject",
            rule="order_not_confirmed",
            message="Read the order back first.",
        )
    )

    (step,), _ = steps_of(log.events)

    assert (step.decision, step.rule) == ("reject", "order_not_confirmed")


def test_a_tool_that_failed_before_any_decision_shows_its_error() -> None:
    error = {"code": "unknown_item", "message": "There is no menu item 'lobster'."}
    log = (
        Log()
        .call("c1", "get_item_details", item_code="lobster")
        .result("c1", "get_item_details", error=error)
    )

    (step,), _ = steps_of(log.events)

    assert (step.decision, step.rule) == (None, "unknown_item")
    assert step.message == "There is no menu item 'lobster'."


def test_a_lookup_has_no_decision() -> None:
    log = Log().call("c1", "get_cart").result("c1", "get_cart", cart={"lines": []})

    (step,), _ = steps_of(log.events)

    assert (step.tool, step.decision, step.rule, step.message) == (
        "get_cart",
        None,
        None,
        None,
    )


def test_two_calls_in_one_round_keep_their_own_arguments() -> None:
    log = (
        Log()
        .call("a", "add_item", item_code="fries")
        .call("b", "add_item", item_code="cola")
        .result("a", "add_item", decision="allow")
        .result("b", "add_item", decision="allow")
    )

    steps, _ = steps_of(log.events)

    assert [step.arguments["item_code"] for step in steps] == ["fries", "cola"]


def test_a_call_with_no_result_yet_is_left_out() -> None:
    """The turn is still running. There is nothing to say about it yet."""
    log = Log().call("c1", "add_item", item_code="fries")

    assert steps_of(log.events) == ([], 0)


def test_a_timer_firing_is_a_step() -> None:
    log = Log().timer("hand_off", "time_limit", 180)

    (step,), _ = steps_of(log.events)

    assert (step.tool, step.decision, step.rule) == ("timer", None, "time_limit")
    assert step.arguments == {"after_seconds": 180}
    assert step.message == "hand off"


def test_steps_keep_the_most_recent_and_count_the_rest() -> None:
    log = Log()
    for number in range(14):
        log.call(f"c{number}", "get_cart").result(f"c{number}", "get_cart")
    log.timer("nudge", "silence", 20)

    steps, earlier = steps_of(log.events)

    assert len(steps) == 10
    assert earlier == 5
    assert steps[-1].tool == "timer"


# --- Reason and what to do next ---------------------------------------------


def test_each_reason_has_a_heading_a_person_can_read() -> None:
    assert reason_title("extreme_quantity") == "Very large order"
    assert reason_title("human_requested") == "A person was asked for"
    assert reason_title("time_limit") == "Took longer than the time limit"


def test_an_unknown_reason_still_reads_as_words() -> None:
    assert reason_title("allergen_unverified") == "Allergen unverified"


def test_a_very_large_order_says_what_was_asked_for() -> None:
    action = {"type": "add_item", "item_code": "fries", "quantity": 1000}
    details = {"line_quantity": 1000, "cart_item_count": 1000}

    sentence = outstanding(
        "extreme_quantity", details, action, item_name="Fries", cart_item_count=0
    )

    assert sentence == (
        "The customer asked for 1000 of Fries and confirmed it. That is more "
        "than the agent may take, so it was not added. Check the quantity with "
        "them. The cart is empty."
    )


def test_a_very_large_change_names_the_line() -> None:
    action = {"type": "update_line", "line_no": 2, "quantity": 60}

    sentence = outstanding(
        "extreme_quantity", {}, action, item_name=None, cart_item_count=3
    )

    assert sentence.startswith(
        "The customer asked to change line L2 to 60 and confirmed it."
    )
    assert sentence.endswith("The cart holds 3 items and can be placed as it stands.")


def test_a_request_for_a_person_quotes_the_reason_given() -> None:
    sentence = outstanding(
        "human_requested",
        {"reason": "customer wants to pay with a gift card"},
        None,
        item_name=None,
        cart_item_count=1,
    )

    assert sentence == (
        'A person was asked for. The reason given: "customer wants to pay with '
        'a gift card". Carry on from where the conversation stopped. The cart '
        "holds 1 item and can be placed as it stands."
    )


def test_a_request_with_no_reason_does_not_quote_one() -> None:
    sentence = outstanding(
        "human_requested", {"reason": "  "}, None, item_name=None, cart_item_count=0
    )

    assert "reason given" not in sentence
    assert sentence.startswith("A person was asked for. Carry on")


def test_the_time_limit_says_to_finish_the_order() -> None:
    sentence = outstanding("time_limit", {}, None, item_name=None, cart_item_count=4)

    assert sentence == (
        "The order was not finished within the time limit. Finish it with the "
        "customer. The cart holds 4 items and can be placed as it stands."
    )


def test_a_reason_added_later_still_gets_a_usable_sentence() -> None:
    sentence = outstanding(
        "allergen_unverified", {}, None, item_name=None, cart_item_count=0
    )

    assert sentence == (
        "Handed off by the rule 'allergen_unverified'. Read the conversation "
        "and carry on from there. The cart is empty."
    )
