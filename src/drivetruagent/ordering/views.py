"""The cart and the order as callers see them.

Every cart change returns a full CartSnapshot, priced. Nothing downstream (the
model, the order screen, a staff dashboard) has to remember earlier state or
do its own arithmetic.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.enums import Language, SessionState

# Used when a session's language has not been detected yet, and as the
# fallback when a name has no entry in the session's language.
DEFAULT_LANGUAGE = Language.EN


def localize(name: Mapping[str, str], language: Language) -> str:
    """Pick the name in the given language, falling back to English."""
    return name.get(language.value) or name[DEFAULT_LANGUAGE.value]


class SelectedOptionView(BaseModel):
    group_code: str
    group_name: str
    option_code: str
    option_name: str
    price_delta_cents: int


class LineView(BaseModel):
    # How the agent and the customer refer to a line: "L2", "the second one".
    line_no: int
    item_code: str
    item_name: str
    quantity: int
    options: list[SelectedOptionView]
    unit_price_cents: int
    line_total_cents: int


class CartSnapshot(BaseModel):
    session_id: uuid.UUID
    state: SessionState
    language: Language
    # Goes up by one on every change; a consumer can drop stale snapshots.
    version: int
    lines: list[LineView]
    # Total number of items, counting quantities: two fries and a cola is 3.
    item_count: int
    subtotal_cents: int
    tax_cents: int
    total_cents: int


class OrderView(BaseModel):
    order_id: uuid.UUID
    # The short number shown to the customer.
    order_number: int
    session_id: uuid.UUID
    language: Language
    lines: list[LineView]
    item_count: int
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    submitted_at: datetime


class SessionView(BaseModel):
    # Built straight from the database row.
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID = Field(validation_alias="id")
    lane: str
    state: SessionState
    # None until the customer's language has been detected or set.
    language: Language | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None


# --- Menu -------------------------------------------------------------------


class MenuItemSummary(BaseModel):
    item_code: str
    item_name: str
    # The price with the cheapest choices; options may add to it.
    base_price_cents: int
    # Codes of the choices the customer has to be asked about. Empty means the
    # item can be added straight away.
    must_ask: list[str]


class MenuMatch(MenuItemSummary):
    category_code: str
    # 100 is an exact match. Several matches with the same score mean the
    # customer was vague, and should be asked which one they meant.
    score: int


class ChoiceOptionView(BaseModel):
    option_code: str
    option_name: str
    price_delta_cents: int


class ChoiceGroupView(BaseModel):
    group_code: str
    group_name: str
    min_select: int
    max_select: int
    must_ask: bool
    # Filled in by the backend when the customer says nothing about this choice.
    default_option_code: str | None
    options: list[ChoiceOptionView]


class ItemDetails(MenuItemSummary):
    category_code: str
    description: str | None
    available: bool
    choices: list[ChoiceGroupView]


class CategoryView(BaseModel):
    category_code: str
    category_name: str
    items: list[MenuItemSummary]


class MenuView(BaseModel):
    categories: list[CategoryView]
