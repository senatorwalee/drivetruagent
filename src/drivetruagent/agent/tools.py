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

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import tracing
from drivetruagent.agent.model import ToolDefinition
from drivetruagent.enums import Allergen, Language
from drivetruagent.knowledge.embedding import EmbeddingUnavailable
from drivetruagent.knowledge.store import KnowledgeSearch, Retrieval
from drivetruagent.ordering import allergen_matrix
from drivetruagent.ordering import cart as cart_rules
from drivetruagent.ordering import menu as menu_rules
from drivetruagent.ordering import offers as offer_rules
from drivetruagent.ordering import sessions as session_rules
from drivetruagent.ordering.allergens import allergens_named, is_about_allergens
from drivetruagent.ordering.catalog import load_item_by_code
from drivetruagent.ordering.errors import OrderingError, UnknownItem
from drivetruagent.ordering.inventory import DEFAULT_RESERVATION_TTL
from drivetruagent.ordering.offers import SpecialsCache
from drivetruagent.ordering.views import DEFAULT_LANGUAGE, localize
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

logger = logging.getLogger(__name__)

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
    # Where the day's specials are kept between questions. None: read each time.
    specials_cache: SpecialsCache | None = None
    # How approved documents are searched. None: nothing is found.
    knowledge: KnowledgeSearch | None = None


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


class KnowledgeArgs(Args):
    query: str
    item_code: str | None


class AllergenArgs(Args):
    item_code: str | None
    selections: list[Selection] | None
    allergens: list[Allergen] | None


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


async def _get_todays_specials(context: ToolContext, args: NoArgs) -> ToolOutput:
    customer_session = await session_rules.get_session(context.db, context.session_id)
    language = customer_session.language or DEFAULT_LANGUAGE
    # The session's own date, not the clock: a customer who was told about a
    # special just before midnight hears the same answer just after.
    specials = await offer_rules.todays_specials(
        context.db,
        customer_session.business_date,
        language,
        cache=context.specials_cache,
    )
    combos = await offer_rules.list_combos(context.db, language)
    return {
        "specials": [special.model_dump(mode="json") for special in specials],
        "combos": [combo.model_dump(mode="json") for combo in combos],
        "message": _deals_message(bool(specials), bool(combos)),
    }


def _deals_message(has_specials: bool, has_combos: bool) -> str:
    if not has_specials and not has_combos:
        return "There are no specials or combos today. Say so, and offer the menu."
    opening = (
        "Tell the customer today's special in a sentence. Mention the combo "
        "only if they asked about deals or combos: one offer at a time keeps "
        '"one of those" clear.'
        if has_specials
        else "There is no special today. Say so, and mention the combo."
    )
    return (
        f"{opening} These savings are applied for you when the items are "
        "ordered: add the items as usual and the cart will show the saving."
    )


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


ALLERGEN_INSTRUCTIONS = (
    "Tell the customer what is listed, naming allergens in plain words, and "
    "mention anything it may contain and why. Never say something is safe, "
    "allergen-free or fine for them: say it is not listed as containing it. "
    "If they mention a serious allergy, offer to get a team member."
)


async def _check_allergens(context: ToolContext, args: AllergenArgs) -> ToolOutput:
    language = await _session_language(context)
    if args.item_code is not None:
        selections = _as_mapping(args.selections or [])
        reports = [
            await allergen_matrix.item_allergens(
                context.db, args.item_code, selections, args.allergens, language
            )
        ]
    else:
        reports = await allergen_matrix.cart_allergens(
            context.db,
            context.session_id,
            args.allergens,
            language,
            tax_rate=context.tax_rate,
        )
        if not reports:
            return {
                "items": [],
                "message": "The order is empty. Ask which item they mean.",
            }

    unverified = {r.item_code: r.item_name for r in reports if not r.verified}
    if unverified:
        outcome = await gateway.allergen_unverified(
            context.db,
            context.session_id,
            unverified,
            [allergen.value for allergen in args.allergens or []],
            tax_rate=context.tax_rate,
        )
        return outcome.model_dump(mode="json", exclude_none=True)
    return {
        "items": [report.model_dump(mode="json") for report in reports],
        "message": ALLERGEN_INSTRUCTIONS,
    }


KNOWLEDGE_INSTRUCTIONS = (
    "Answer only from these passages, in a sentence or two. If they do not "
    "answer the question, say you do not know. Never call anything safe or "
    "allergen-free."
)
NO_SOURCE = (
    "No approved information answers this. Say you do not know, and offer to "
    "get a team member if it matters to the customer."
)


async def _search_knowledge(context: ToolContext, args: KnowledgeArgs) -> ToolOutput:
    items: dict[str, str] = {}
    if args.item_code is not None:
        item = await load_item_by_code(context.db, args.item_code)
        if item is None:
            raise UnknownItem(args.item_code)
        # For the handoff, which staff read in English.
        items[item.code] = localize(item.name, DEFAULT_LANGUAGE)

    retrieval: Retrieval | None = None
    if context.knowledge is not None:
        with tracing.span(
            "knowledge search",
            OpenInferenceSpanKindValues.RETRIEVER,
            session_id=context.session_id,
            input_value=args.query,
        ) as search_span:
            try:
                retrieval = await context.knowledge.find(
                    context.db, args.query, item_code=args.item_code
                )
            except EmbeddingUnavailable as error:
                logger.warning("Knowledge search unavailable: %s", error)
                tracing.set_error(search_span, str(error))
            else:
                search_span.set_attributes(
                    tracing.document_attributes(
                        [
                            {
                                "id": hit.chunk_id,
                                "score": hit.score,
                                "text": hit.text,
                                "document": hit.document,
                            }
                            for hit in retrieval.hits
                        ]
                    )
                    | tracing.metadata(
                        {
                            "model": retrieval.model,
                            "min_score": retrieval.min_score,
                            "best_score": retrieval.best_score,
                            "item_code": args.item_code,
                        }
                    )
                )
    # Kept in the event log with the result: which model scored what.
    trace = {
        "retrieval": {
            "model": retrieval.model if retrieval else None,
            "min_score": retrieval.min_score if retrieval else None,
            "best_score": retrieval.best_score if retrieval else None,
        }
    }

    if retrieval is not None and retrieval.hits:
        return {
            "sources": [
                {
                    "chunk_id": str(hit.chunk_id),
                    "document": hit.document,
                    "heading": hit.heading,
                    "text": hit.text,
                    "score": hit.score,
                }
                for hit in retrieval.hits
            ],
            "message": KNOWLEDGE_INSTRUCTIONS,
            **trace,
        }
    if is_about_allergens(args.query):
        # An allergen question with no checked answer is not guessed at.
        outcome = await gateway.allergen_unverified(
            context.db,
            context.session_id,
            items,
            sorted(allergen.value for allergen in allergens_named(args.query)),
            question=args.query,
            tax_rate=context.tax_rate,
        )
        return {
            **outcome.model_dump(mode="json", exclude_none=True),
            "sources": [],
            **trace,
        }
    return {"sources": [], "message": NO_SOURCE, **trace}


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
        "get_todays_specials",
        "Get today's specials and the combo deals, with prices. Use it when "
        "the customer asks about specials, deals, combos or how to save.",
        _NO_ARGUMENTS,
        NoArgs,
        _get_todays_specials,
    ),
    _tool(
        "check_allergens",
        "Look up allergens in the restaurant's allergen information. Use it "
        "for every allergen or dietary question; never answer one from memory "
        "or from a description. Give an item to check it with the customer's "
        "choices (unchosen choices count as their defaults), or null to check "
        "every line in the order with the choices on each line.",
        _object(
            {
                "item_code": {
                    "type": ["string", "null"],
                    "description": "The item's code, or null for the whole order.",
                },
                "selections": {
                    "anyOf": [_SELECTIONS, {"type": "null"}],
                    "description": "Choices to take into account, or null.",
                },
                "allergens": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [allergen.value for allergen in Allergen],
                            },
                        },
                        {"type": "null"},
                    ],
                    "description": (
                        "The allergens asked about, or null for all. Dairy is "
                        "'milk'; nuts are 'peanut' and 'tree_nut'."
                    ),
                },
            }
        ),
        AllergenArgs,
        _check_allergens,
    ),
    _tool(
        "search_knowledge",
        "Search the restaurant's approved information about ingredients, "
        "preparation, frying, nutrition and diets (vegetarian, vegan, halal, "
        "gluten). Use it for questions check_allergens cannot answer, such as "
        "what oil is used or what is in a sauce, and answer only from what it "
        "returns.",
        _object(
            {
                "query": {
                    "type": "string",
                    "description": "The customer's question, in their words.",
                },
                "item_code": {
                    "type": ["string", "null"],
                    "description": "The item it is about, or null.",
                },
            }
        ),
        KnowledgeArgs,
        _search_knowledge,
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
    with tracing.span(
        f"tool {name}",
        OpenInferenceSpanKindValues.TOOL,
        session_id=context.session_id,
        input_value=arguments,
        attributes={SpanAttributes.TOOL_NAME: name},
    ) as tool_span:
        output = await _run_tool(context, name, arguments)
        tracing.set_output(tool_span, output)
        error = output.get("error")
        tool_span.set_attributes(
            tracing.metadata(
                {
                    "decision": output.get("decision"),
                    "rule": output.get("rule"),
                    "error": error.get("code") if isinstance(error, dict) else None,
                }
            )
        )
        return output


async def _run_tool(
    context: ToolContext, name: str, arguments: dict[str, Any]
) -> ToolOutput:
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
