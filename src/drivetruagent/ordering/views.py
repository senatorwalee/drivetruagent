"""The cart and the order as callers see them.

Every cart change returns a full CartSnapshot, priced. Nothing downstream (the
model, the order screen, a staff dashboard) has to remember earlier state or
do its own arithmetic.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel

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
