"""What the model is told about the tools. No database."""

from typing import Any

import pytest

from drivetruagent.agent.tools import TOOL_DEFINITIONS, TOOLS, Tool

EXPECTED_TOOLS = {
    "search_menu",
    "get_item_details",
    "add_item",
    "update_item",
    "remove_item",
    "get_cart",
    "request_confirmation",
    "submit_order",
    "confirm_pending_action",
    "cancel_pending_action",
    "set_language",
    "request_human",
}

# Words that would mean a tool lets the model set money.
MONEY_WORDS = ("price", "cents", "discount", "total", "amount", "cost")


def objects_in(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object schema inside a schema, however deeply nested."""
    found: list[dict[str, Any]] = []
    if schema.get("type") == "object":
        found.append(schema)
    for value in schema.values():
        children = value if isinstance(value, list) else [value]
        for child in children:
            if isinstance(child, dict):
                found.extend(objects_in(child))
    return found


def property_names(schema: dict[str, Any]) -> set[str]:
    return {name for obj in objects_in(schema) for name in obj["properties"]}


def test_the_tool_list_is_exactly_what_is_expected() -> None:
    assert {tool.name for tool in TOOL_DEFINITIONS} == EXPECTED_TOOLS


def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in TOOL_DEFINITIONS]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.definition.name)
def test_every_tool_says_when_to_use_it(tool: Tool[Any]) -> None:
    assert len(tool.definition.description) > 20


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.definition.name)
def test_every_schema_is_strict(tool: Tool[Any]) -> None:
    """Strict form: all properties required, and no others allowed, at every level."""
    schema = tool.definition.parameters

    assert schema["type"] == "object"
    for obj in objects_in(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.definition.name)
def test_schema_and_argument_check_agree(tool: Tool[Any]) -> None:
    """What the model is told to send is exactly what the check accepts."""
    told = set(tool.definition.parameters["properties"])
    checked = set(tool.args_model.model_fields)

    assert told == checked


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.definition.name)
def test_no_tool_takes_money(tool: Tool[Any]) -> None:
    for name in property_names(tool.definition.parameters):
        assert not any(word in name.lower() for word in MONEY_WORDS), name
