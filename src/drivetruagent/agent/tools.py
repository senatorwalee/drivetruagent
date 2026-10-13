"""The tools the model can use, and what each one runs.

A tool is three things: what the model is told about it (a name, when to use
it, and a strict schema for its arguments), a check of the arguments the
model actually sent, and a handler.

Rules that hold for every tool:

  - A tool never raises for an expected failure. A rejection, an unknown
    item or bad arguments all come back as a result the model can read and
    recover from in the same turn.
  - No tool takes a price, a discount or a total.
  - Anything that changes an order goes through the policy gateway.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.model import ToolDefinition
from drivetruagent.enums import Language
from drivetruagent.ordering import cart as cart_rules
from drivetruagent.ordering import menu as menu_rules
from drivetruagent.ordering import sessions as session_rules
from drivetruagent.ordering.errors import OrderingError
from drivetruagent.ordering.inventory import DEFAULT_RESERVATION_TTL
from drivetruagent.ordering.views import DEFAULT_LANGUAGE
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import (
    Action,
    AddItem,
    RemoveLine,
    RequestConfirmation,
    SubmitOrder,
    UpdateLine,
)
from drivetruagent.policy.decisions import PolicyLimits

INVALID_TOOL_CALL = "invalid_tool_call"

type ToolOutput = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    """What a handler needs besides its arguments."""

    db: AsyncSession
    session_id: uuid.UUID
    tax_rate: Decimal
    limits: PolicyLimits
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL
    # Identifies this one tool call, so running it twice changes things once.
    idempotency_key: str | None = None


# --- Arguments --------------------------------------------------------------


class Args(BaseModel):
    # extra="forbid": a field the tool does not define is a mistake, not noise.
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


class SearchMenuArgs(Args):
    query: str


class ItemArgs(Args):
    item_code: str


class Selection(Args):
    """One choice: the group ("size") and the option or options picked."""

    group: str
    options: list[str]


class AddItemArgs(Args):
    item_code: str
    quantity: int
    selections: list[Selection]


class UpdateItemArgs(Args):
    line_no: int
    quantity: int | None
    selections: list[Selection] | None


class LineArgs(Args):
    line_no: int


class PendingActionArgs(Args):
    pending_action_id: uuid.UUID


class LanguageArgs(Args):
    language: Language


class HumanArgs(Args):
    reason: str


def _as_mapping(selections: list[Selection]) -> dict[str, list[str]]:
    """Turn the list form the model sends into {group: [options]}.

    The model sends a list because a strict schema cannot describe an object
    whose keys are free text. If a group appears twice, its options combine.
    """
    mapping: dict[str, list[str]] = {}
    for selection in selections:
        mapping.setdefault(selection.group, []).extend(selection.options)
    return mapping


# --- Handlers ---------------------------------------------------------------


async def _session_language(context: ToolContext) -> Language:
    customer_session = await session_rules.get_session(context.db, context.session_id)
    return customer_session.language or DEFAULT_LANGUAGE


async def _propose(context: ToolContext, action: Action) -> ToolOutput:
    outcome = await gateway.execute(
        context.db,
        context.session_id,
        action,
        tax_rate=context.tax_rate,
        limits=context.limits,
        reservation_ttl=context.reservation_ttl,
        idempotency_key=context.idempotency_key,
    )
    return outcome.model_dump(mode="json", exclude_none=True)


async def _search_menu(context: ToolContext, args: SearchMenuArgs) -> ToolOutput:
    language = await _session_language(context)
    matches = await menu_rules.search_menu(context.db, args.query, language)
    return {"matches": [match.model_dump(mode="json") for match in matches]}


async def _get_item_details(context: ToolContext, args: ItemArgs) -> ToolOutput:
    language = await _session_language(context)
    details = await menu_rules.get_item_details(context.db, args.item_code, language)
    return details.model_dump(mode="json")


async def _add_item(context: ToolContext, args: AddItemArgs) -> ToolOutput:
    return await _propose(
        context,
        AddItem(
            item_code=args.item_code,
            quantity=args.quantity,
            selections=_as_mapping(args.selections),
        ),
    )


async def _update_item(context: ToolContext, args: UpdateItemArgs) -> ToolOutput:
    selections = None if args.selections is None else _as_mapping(args.selections)
    return await _propose(
        context,
        UpdateLine(line_no=args.line_no, quantity=args.quantity, selections=selections),
    )


async def _remove_item(context: ToolContext, args: LineArgs) -> ToolOutput:
    return await _propose(context, RemoveLine(line_no=args.line_no))


async def _get_cart(context: ToolContext, args: NoArgs) -> ToolOutput:
    snapshot = await cart_rules.get_cart(
        context.db, context.session_id, tax_rate=context.tax_rate
    )
    return {"cart": snapshot.model_dump(mode="json")}


async def _request_confirmation(context: ToolContext, args: NoArgs) -> ToolOutput:
    return await _propose(context, RequestConfirmation())


async def _submit_order(context: ToolContext, args: NoArgs) -> ToolOutput:
    return await _propose(context, SubmitOrder())


async def _confirm_pending_action(
    context: ToolContext, args: PendingActionArgs
) -> ToolOutput:
    outcome = await gateway.confirm(
        context.db,
        context.session_id,
        args.pending_action_id,
        tax_rate=context.tax_rate,
        limits=context.limits,
        reservation_ttl=context.reservation_ttl,
        idempotency_key=context.idempotency_key,
    )
    return outcome.model_dump(mode="json", exclude_none=True)


async def _cancel_pending_action(
    context: ToolContext, args: PendingActionArgs
) -> ToolOutput:
    outcome = await gateway.cancel(
        context.db,
        context.session_id,
        args.pending_action_id,
        tax_rate=context.tax_rate,
    )
    return outcome.model_dump(mode="json", exclude_none=True)


async def _set_language(context: ToolContext, args: LanguageArgs) -> ToolOutput:
    await session_rules.set_language(context.db, context.session_id, args.language)
    return {
        "language": args.language.value,
        "message": "Language set. Item and option names will now be in this language.",
    }


async def _request_human(context: ToolContext, args: HumanArgs) -> ToolOutput:
    outcome = await gateway.hand_to_human(
        context.db, context.session_id, args.reason, tax_rate=context.tax_rate
    )
    return outcome.model_dump(mode="json", exclude_none=True)


# --- Schemas ----------------------------------------------------------------
# Written by hand, in the strict form: every property listed in `required`,
# and additionalProperties false, at every level. A value that may be left
# out is typed as "X or null" instead. A test checks every schema obeys this.


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_NO_ARGUMENTS = _object({})

_SELECTIONS = {
    "type": "array",
    "description": (
        "The customer's choices for this item, one entry per choice. Use the "
        "group and option codes from the menu, not the display names."
    ),
    "items": _object(
        {
            "group": {
                "type": "string",
                "description": "The choice's code, such as 'size'.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The option code or codes picked, such as ['large'].",
            },
        }
    ),
}

_ITEM_CODE = {"type": "string", "description": "The item's code, such as 'fries'."}
_LINE_NO = {
    "type": "integer",
    "description": "The line number shown in the cart, such as 2 for line L2.",
}
_PENDING_ACTION_ID = {
    "type": "string",
    "description": "The pending_action_id from the result that asked for confirmation.",
}


@dataclass(frozen=True)
class Tool[ArgsT: Args]:
    definition: ToolDefinition
    args_model: type[ArgsT]
    handler: Callable[[ToolContext, ArgsT], Awaitable[ToolOutput]]


def _tool[ArgsT: Args](
    name: str,
    description: str,
    parameters: dict[str, Any],
    args_model: type[ArgsT],
    handler: Callable[[ToolContext, ArgsT], Awaitable[ToolOutput]],
) -> Tool[ArgsT]:
    return Tool(ToolDefinition(name, description, parameters), args_model, handler)


TOOLS: tuple[Tool[Any], ...] = (
    _tool(
        "search_menu",
        "Find menu items matching what the customer said. Use it when their "
        "words do not clearly match one item in the menu index. Several "
        "results with the same score mean the customer was vague: ask which "
        "one they want.",
        _object(
            {
                "query": {
                    "type": "string",
                    "description": "The customer's words for the item, any language.",
                }
            }
        ),
        SearchMenuArgs,
        _search_menu,
    ),
    _tool(
        "get_item_details",
        "Get one item's choices: which must be asked, which have defaults, the "
        "options for each and what they add to the price. Use it to answer "
        "questions about an item or before asking the customer to choose.",
        _object({"item_code": _ITEM_CODE}),
        ItemArgs,
        _get_item_details,
    ),
    _tool(
        "add_item",
        "Add an item to the order as a new line. Pass only the choices the "
        "customer actually made; defaults are filled in for you. If a required "
        "choice is missing, the result says which and lists the options: ask "
        "the customer, then call this again with their answer.",
        _object(
            {
                "item_code": _ITEM_CODE,
                "quantity": {"type": "integer", "description": "How many. Usually 1."},
                "selections": _SELECTIONS,
            }
        ),
        AddItemArgs,
        _add_item,
    ),
    _tool(
        "update_item",
        "Change a line already in the order: its quantity, its choices, or "
        "both. Pass null for whichever is not changing. Only the choices you "
        "name change; the others stay as they are.",
        _object(
            {
                "line_no": _LINE_NO,
                "quantity": {
                    "type": ["integer", "null"],
                    "description": "The new quantity, or null to leave it.",
                },
                "selections": {
                    "anyOf": [_SELECTIONS, {"type": "null"}],
                    "description": "The choices to change, or null to leave them.",
                },
            }
        ),
        UpdateItemArgs,
        _update_item,
    ),
    _tool(
        "remove_item",
        "Remove a line from the order.",
        _object({"line_no": _LINE_NO}),
        LineArgs,
        _remove_item,
    ),
    _tool(
        "get_cart",
        "Get the current order with prices, tax and total.",
        _NO_ARGUMENTS,
        NoArgs,
        _get_cart,
    ),
    _tool(
        "request_confirmation",
        "Call this when the customer has finished ordering, just before you "
        "read the order and total back to them. It is required before "
        "submit_order.",
        _NO_ARGUMENTS,
        NoArgs,
        _request_confirmation,
    ),
    _tool(
        "submit_order",
        "Place the order. Only after you have read it back and the customer "
        "has said it is correct.",
        _NO_ARGUMENTS,
        NoArgs,
        _submit_order,
    ),
    _tool(
        "confirm_pending_action",
        "The customer said yes to something you were told to confirm with them.",
        _object({"pending_action_id": _PENDING_ACTION_ID}),
        PendingActionArgs,
        _confirm_pending_action,
    ),
    _tool(
        "cancel_pending_action",
        "The customer said no to something you were told to confirm with them.",
        _object({"pending_action_id": _PENDING_ACTION_ID}),
        PendingActionArgs,
        _cancel_pending_action,
    ),
    _tool(
        "set_language",
        "Record the language the customer is speaking, as soon as you know it "
        "and whenever it changes. Names in later results follow it.",
        _object(
            {
                "language": {
                    "type": "string",
                    "enum": [language.value for language in Language],
                    "description": "'en' for English, 'fr' for French.",
                }
            }
        ),
        LanguageArgs,
        _set_language,
    ),
    _tool(
        "request_human",
        "Hand the conversation to a team member. Use it when the customer asks "
        "for a person, or when you cannot help them after trying.",
        _object(
            {
                "reason": {
                    "type": "string",
                    "description": "A few words on why, for the team member.",
                }
            }
        ),
        HumanArgs,
        _request_human,
    ),
)

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = tuple(tool.definition for tool in TOOLS)
_TOOLS_BY_NAME = {tool.definition.name: tool for tool in TOOLS}


# --- Running a tool ---------------------------------------------------------


async def run_tool(
    context: ToolContext, name: str, arguments: dict[str, Any]
) -> ToolOutput:
    """Run one tool call from the model and return what it should be shown."""
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return _error(
            INVALID_TOOL_CALL,
            f"There is no tool called '{name}'.",
            {"valid_tools": sorted(_TOOLS_BY_NAME)},
        )
    try:
        args = tool.args_model.model_validate(arguments)
    except ValidationError as error:
        problems = [
            {
                "field": ".".join(str(part) for part in problem["loc"]),
                "problem": problem["msg"],
            }
            for problem in error.errors()
        ]
        return _error(
            INVALID_TOOL_CALL,
            f"The arguments for '{name}' are not valid. Fix them and call it again.",
            {"problems": problems},
        )
    try:
        return await tool.handler(context, args)
    except OrderingError as error:
        # Raised by the read-only tools and by an unknown pending action. The
        # gateway already turns ordering errors into rejections for the rest.
        return _error(error.code, error.message, error.details)


def _error(code: str, message: str, details: dict[str, Any]) -> ToolOutput:
    return {"error": {"code": code, "message": message, "details": details}}
