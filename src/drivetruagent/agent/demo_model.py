"""A stand-in model that plays one fixed conversation, for demos without a key.

Select it with AGENT_MODEL=demo. Whatever the customer types, the model makes
the next move of a set script: a missing size, two items in one breath, an
unusual quantity that is held and then corrected, the read-back, and the
order being placed.

Only the model is scripted. Every tool call it makes runs for real through
the same policy engine and ordering rules, and everything it says about
totals and order numbers is read from what the tools returned.
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
    "Can I get some fries?",
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


def _read_back(results: list[ToolResult]) -> ModelTurn:
    cart = results[-1].result.get("cart", {})
    items = ", ".join(
        f"{line['quantity']} {line['item_name'].lower()}"
        for line in cart.get("lines", [])
    )
    return ModelTurn(
        text=f"So that's {items}. Your total is {_money(cart.get('total_cents', 0))}. "
        "Is that right?",
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


# For each customer turn: tool results seen so far this turn -> the next move.
_SCRIPT: tuple[dict[int, Move], ...] = (
    {0: _say("Welcome! What can I get you today?")},
    {
        # No size given: the backend refuses and says what to ask.
        0: _call("add_item", item_code="fries", quantity=1, selections=[]),
        1: _say("Sure. What size would you like?"),
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
        2: _say("Large fries and a medium cola. Anything else?"),
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

        # Turn five starts by cancelling the held action, which needs an id
        # from the previous turn's results, so it looks at the whole history.
        if turn_index == 4 and not results_this_turn:
            return _cancel_held_action(
                [m for m in messages if isinstance(m, ToolResult)]
            )

        move = _SCRIPT[turn_index].get(len(results_this_turn))
        if move is None:
            return ModelTurn(text=FINISHED, model=MODEL_NAME)
        return move(results_this_turn)
