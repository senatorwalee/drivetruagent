"""A stand-in model that plays one fixed conversation, for demos without a key.

Select it with AGENT_MODEL=demo. Whatever the customer types, the model makes
the next move of a set script: a combo offered and taken up, a missing size,
two items in one breath, an unusual quantity that is held and then corrected,
the read-back, and the order being placed.

Only the model is scripted. Every tool call it makes runs for real through
the same policy engine and ordering rules, and everything it says about
totals, savings and order numbers is read from what the tools returned.
"""

from collections.abc import Callable, Sequence
from typing import Any

from drivetruagent.agent.messages import Message, ToolCall, ToolResult, UserMessage
from drivetruagent.agent.model import ModelTurn, ToolDefinition

MODEL_NAME = "demo-script"

# What to type at each turn to make the conversation read naturally. The
# script advances whatever is typed; these are shown to the presenter.
SUGGESTED_LINES: tuple[str, ...] = (
    "Hi",
    "A cheeseburger, please.",
    "Sure, some fries.",
    "Large, and a medium cola.",
    "Fifteen cookies.",
    "No, just two.",
    "That's all.",
    "Yes.",
)

FINISHED = "That's the end of this demo. Start a new session to run it again."

# One move: given the tool results seen so far this turn, the model's next step.
type Move = Callable[[list[ToolResult]], ModelTurn]


def _say(text: str) -> Move:
    return lambda results: ModelTurn(text=text, model=MODEL_NAME)


def _call(name: str, **arguments: Any) -> Move:
    return lambda results: _tool_calls((name, arguments))


def _tool_calls(*calls: tuple[str, dict[str, Any]]) -> ModelTurn:
    return ModelTurn(
        tool_calls=tuple(
            ToolCall(call_id=f"demo_{name}_{index}", name=name, arguments=arguments)
            for index, (name, arguments) in enumerate(calls)
        ),
        model=MODEL_NAME,
    )


def _size(option: str) -> list[dict[str, Any]]:
    return [{"group": "size", "options": [option]}]


def _cancel_held_action(results: list[ToolResult]) -> ModelTurn:
    # The id comes from the conversation, exactly as a real model would get it.
    held = next((r for r in reversed(results) if "pending_action_id" in r.result), None)
    if held is None:
        return ModelTurn(
            text="Sorry, I lost track of that. What would you like?", model=MODEL_NAME
        )
    return _tool_calls(
        (
            "cancel_pending_action",
            {"pending_action_id": held.result["pending_action_id"]},
        )
    )


def _offer_what_was_suggested(results: list[ToolResult]) -> ModelTurn:
    # Whether there is anything to offer is the backend's call. The model
    # only puts words to it.
    suggestion = results[-1].result.get("suggestion")
    if suggestion is None:
        return ModelTurn(text="One cheeseburger. Anything else?", model=MODEL_NAME)
    missing = " and ".join(
        f"a {_singular(category['category_name'])}"
        for category in suggestion["missing"]
    )
    return ModelTurn(
        text=f"One cheeseburger. Add {missing} to make it a "
        f"{suggestion['name'].lower()} and save {_money(suggestion['saves_cents'])}?",
        model=MODEL_NAME,
    )


def _say_what_was_saved(results: list[ToolResult]) -> ModelTurn:
    cart = results[-1].result.get("cart", {})
    text = "Large fries and a medium cola."
    for discount in cart.get("discounts", []):
        text += (
            f" That makes it a {discount['name'].lower()}, so you save "
            f"{_money(discount['amount_cents'])}."
        )
    return ModelTurn(text=f"{text} Anything else?", model=MODEL_NAME)


def _read_back(results: list[ToolResult]) -> ModelTurn:
    cart = results[-1].result.get("cart", {})
    items = ", ".join(
        f"{line['quantity']} {line['item_name'].lower()}"
        for line in cart.get("lines", [])
    )
    saved = cart.get("discount_cents", 0)
    savings = f" You saved {_money(saved)}." if saved else ""
    return ModelTurn(
        text=f"So that's {items}.{savings} Your total is "
        f"{_money(cart.get('total_cents', 0))}. Is that right?",
        model=MODEL_NAME,
    )


def _order_placed(results: list[ToolResult]) -> ModelTurn:
    order = results[-1].result.get("order")
    if order is None:
        return ModelTurn(
            text="I couldn't place that order. Let me get someone to help.",
            model=MODEL_NAME,
        )
    return ModelTurn(
        text=f"Thanks! Your order number is {order['order_number']} and your total "
        f"is {_money(order['total_cents'])}. Please pull forward.",
        model=MODEL_NAME,
    )


def _money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _singular(category_name: str) -> str:
    """ "Sides" -> "side". Enough for the demo's two categories."""
    return category_name.lower().removesuffix("s")


# For each customer turn: tool results seen so far this turn -> the next move.
_SCRIPT: tuple[dict[int, Move], ...] = (
    {0: _say("Welcome! What can I get you today?")},
    {
        # A burger on its own: the backend suggests the combo.
        0: _call("add_item", item_code="cheeseburger", quantity=1, selections=[]),
        1: _offer_what_was_suggested,
    },
    {
        # No size given: the backend refuses and says what to ask.
        0: _call("add_item", item_code="fries", quantity=1, selections=[]),
        1: _say("Sure. What size fries?"),
    },
    {
        0: lambda results: _tool_calls(
            (
                "add_item",
                {"item_code": "fries", "quantity": 1, "selections": _size("large")},
            ),
            (
                "add_item",
                {"item_code": "cola", "quantity": 1, "selections": _size("medium")},
            ),
        ),
        # The third part of the combo: the saving appears in the cart.
        2: _say_what_was_saved,
    },
    {
        # More than ten of one item: the policy engine holds it for a yes.
        0: _call("add_item", item_code="cookie", quantity=15, selections=[]),
        1: _say("Fifteen cookies, is that right?"),
    },
    {
        1: _call("add_item", item_code="cookie", quantity=2, selections=[]),
        2: _say("Two cookies. Anything else?"),
    },
    {
        0: _call("request_confirmation"),
        1: _read_back,
    },
    {
        0: _call("submit_order"),
        1: _order_placed,
    },
)


# The turn where the customer says no to the fifteen cookies.
_CANCEL_TURN = 5


class DemoModel:
    """Implements ModelClient by replaying the script above."""

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        customer_turns = [m for m in messages if isinstance(m, UserMessage)]
        last_customer_turn = max(
            index for index, m in enumerate(messages) if isinstance(m, UserMessage)
        )
        results_this_turn = [
            m for m in messages[last_customer_turn + 1 :] if isinstance(m, ToolResult)
        ]

        turn_index = len(customer_turns) - 1
        if turn_index >= len(_SCRIPT):
            return ModelTurn(text=FINISHED, model=MODEL_NAME)

        # This turn starts by cancelling the held action, which needs an id
        # from the previous turn's results, so it looks at the whole history.
        if turn_index == _CANCEL_TURN and not results_this_turn:
            return _cancel_held_action(
                [m for m in messages if isinstance(m, ToolResult)]
            )

        move = _SCRIPT[turn_index].get(len(results_this_turn))
        if move is None:
            return ModelTurn(text=FINISHED, model=MODEL_NAME)
        return move(results_this_turn)
