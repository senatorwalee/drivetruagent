"""The cart and the order as callers see them.

Every cart change returns a full CartSnapshot, priced. Nothing downstream (the
model, the order screen, a staff dashboard) has to remember earlier state or
do its own arithmetic.
"""

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from drivetruagent.enums import Channel, DiscountKind, Language, SessionState

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


class DiscountView(BaseModel):
    """An amount taken off, and why. Worked out by the backend, never asked for."""

    kind: DiscountKind
    code: str
    name: str
    # How many times it applied: two combos, or three units of a special.
    times: int
    # The whole amount for this entry, as a positive number of cents.
    amount_cents: int


class CartSnapshot(BaseModel):
    session_id: uuid.UUID
    state: SessionState
    language: Language
    # Goes up by one on every change; a consumer can drop stale snapshots.
    version: int
    lines: list[LineView]
    # Total number of items, counting quantities: two fries and a cola is 3.
    item_count: int
    # The sum of the lines, before discounts.
    subtotal_cents: int
    discounts: list[DiscountView]
    discount_cents: int
    # Charged on the subtotal less the discount.
    tax_cents: int
    # subtotal - discount + tax.
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
    discounts: list[DiscountView]
    discount_cents: int
    tax_cents: int
    total_cents: int
    submitted_at: datetime


class TimersView(BaseModel):
    """When the backend will next act on its own, if nothing else happens.

    A screen counts down to these. It never works the rules out itself. Each
    is None when that timer is not running: all of them once a session has
    ended or while it is paused.
    """

    paused: bool
    # When the current pause began, and how long the session has spent paused
    # before it. With these a screen can show how long the session has run.
    paused_at: datetime | None
    paused_seconds: int
    # The customer will be asked whether they are still there.
    nudge_at: datetime | None
    # They were asked and have not answered: the session will be closed.
    closes_at: datetime | None
    # The session will have run too long, and a person will take over.
    hand_off_at: datetime | None


class SessionView(BaseModel):
    session_id: uuid.UUID
    lane: str
    channel: Channel
    state: SessionState
    # None until the customer's language has been detected or set.
    language: Language | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    # The restaurant's date when the car arrived. Decides the day's specials.
    business_date: date
    timers: TimersView


# --- Menu -------------------------------------------------------------------


class MenuItemSummary(BaseModel):
    item_code: str
    item_name: str
    # The price with the cheapest choices; options may add to it.
    base_price_cents: int
    # Codes of the choices the customer has to be asked about. Empty means the
    # item can be added straight away.
    must_ask: list[str]
    # False when none are left that another session is not already holding.
    in_stock: bool = True


class OfferedElsewhere(BaseModel):
    """Another item that has what this match lacks: "spicy" on the chicken
    burger, "iced" in the iced tea."""

    word: str
    item_code: str
    item_name: str


class MenuMatch(MenuItemSummary):
    category_code: str
    # 100 is an exact match. Several matches with the same score mean the
    # customer was vague, and should be asked which one they meant.
    score: int
    # "exact": everything the customer said is this item or one of its
    # choices. "close": some of their words name something it doesn't have.
    match: Literal["exact", "close"] = "exact"
    # Those words, as the search saw them: ["oat"] for "oat milk shake".
    not_offered: list[str] = Field(default_factory=list)
    offered_elsewhere: list[OfferedElsewhere] = Field(default_factory=list)


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


# --- Offers -----------------------------------------------------------------


class CategoryRef(BaseModel):
    category_code: str
    category_name: str


class ComboView(BaseModel):
    code: str
    name: str
    # One item from each of these, bought together, makes the combo.
    slots: list[CategoryRef]
    saves_cents: int


class SpecialView(BaseModel):
    """An item that is cheaper today. The saving is applied by the backend to
    every one in the cart; nobody has to ask for it."""

    code: str
    name: str
    description: str | None
    item_code: str
    item_name: str
    # Prices with the cheapest choices. Options may add to both.
    regular_price_cents: int
    special_price_cents: int
    saves_cents: int
    # Read live, every time: a special that has sold out is worth knowing about.
    in_stock: bool


class DaySpecialsView(BaseModel):
    date: date
    # ISO weekday: 1 is Monday, 7 is Sunday.
    weekday: int
    day_name: str
    is_today: bool
    specials: list[SpecialView]


class WeekOfSpecialsView(BaseModel):
    """The specials for each day of this week, Monday to Sunday."""

    today: date
    days: list[DaySpecialsView]


class SuggestionView(BaseModel):
    """Something the agent may offer the customer, once.

    The backend decides whether there is anything to suggest. The agent only
    chooses the words.
    """

    kind: DiscountKind
    code: str
    name: str
    # What the customer would still have to add.
    missing: list[CategoryRef]
    saves_cents: int
