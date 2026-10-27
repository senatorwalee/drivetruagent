"""The actions a caller can propose.

Each has a `type` tag, so one field tells them apart in JSON: in an API
request, in the agent's tool call, and in a stored pending action. None of
them carries a price, a discount or a total. Money is never something a
caller can propose.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AddItem(ActionModel):
    type: Literal["add_item"] = "add_item"
    item_code: str = Field(min_length=1)
    quantity: int = 1
    # Group code -> option codes, e.g. {"size": ["large"]}.
    selections: dict[str, list[str]] = Field(default_factory=dict)


class UpdateLine(ActionModel):
    type: Literal["update_line"] = "update_line"
    line_no: int
    quantity: int | None = None
    selections: dict[str, list[str]] | None = None


class RemoveLine(ActionModel):
    type: Literal["remove_line"] = "remove_line"
    line_no: int


class ReplaceLine(ActionModel):
    """Swap a line for another item in one step: "the fish burger instead".
    If the new item can't be added, the line stays as it was."""

    type: Literal["replace_line"] = "replace_line"
    line_no: int
    item_code: str = Field(min_length=1)
    # None: keep the line's quantity.
    quantity: int | None = None
    selections: dict[str, list[str]] = Field(default_factory=dict)


class RequestConfirmation(ActionModel):
    """The agent is about to read the order back and ask if it is right."""

    type: Literal["request_confirmation"] = "request_confirmation"


class SubmitOrder(ActionModel):
    type: Literal["submit_order"] = "submit_order"


Action = Annotated[
    AddItem | UpdateLine | RemoveLine | ReplaceLine | RequestConfirmation | SubmitOrder,
    Field(discriminator="type"),
]

# Parses a dict (from JSON or the database) into the right action class.
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
