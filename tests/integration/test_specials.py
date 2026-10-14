"""Today's specials against a real database and the real menu.

From data/menu.json, one special a day. The two used most here:
  Monday   150 off every chicken burger (649)
  Friday   100 off every milkshake (399 small)

The day is never read from a clock here. Each session is given its business
date, which is how the app itself decides which specials a session gets.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.api.deps import get_business_date
from drivetruagent.cache import TtlCache
from drivetruagent.db.models import MenuItem, OrderDiscount, Special
from drivetruagent.enums import DiscountKind, Language
from drivetruagent.ordering.cart import add_item, get_cart, remove_line
from drivetruagent.ordering.errors import UnknownSpecial
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.offers import (
    SpecialsCache,
    load_specials,
    set_special_active,
    todays_specials,
    week_of_specials,
)
from drivetruagent.ordering.orders import get_order, submit_order
from drivetruagent.ordering.sessions import open_session, set_language
from drivetruagent.ordering.views import CartSnapshot, DiscountView
from drivetruagent.policy.decisions import PolicyLimits

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
MONDAY = date(2026, 10, 12)
THURSDAY = date(2026, 10, 15)
FRIDAY = date(2026, 10, 16)
SUNDAY = date(2026, 10, 18)

CHICKEN_BURGER = ("chicken_burger", {"spice": ["mild"]})
MILKSHAKE = ("milkshake", {"size": ["small"], "flavour": ["vanilla"]})
SMALL_FRIES = ("fries", {"size": ["small"]})
WATER = ("bottled_water", {})

MONDAY_DESCRIPTION = "A dollar fifty off every crispy chicken burger, every Monday."

CHICKEN_SPECIAL = DiscountView(
    kind=DiscountKind.SPECIAL,
    code="chicken_burger_monday",
    name="Chicken burger Monday",
    times=1,
    amount_cents=150,
)

pytestmark = pytest.mark.usefixtures("specials")


class Clock:
    """Seconds that only move when a test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def car_on(db: AsyncSession, day: date, lane: str = "lane-1") -> uuid.UUID:
    session_id = (await open_session(db, lane, business_date=day)).id
    await db.commit()
    return session_id


async def add(
    db: AsyncSession,
    session_id: uuid.UUID,
    item: tuple[str, dict[str, list[str]]],
    quantity: int = 1,
) -> CartSnapshot:
    code, selections = item
    return await add_item(db, session_id, code, quantity, selections, tax_rate=TAX)


async def retire(db: AsyncSession, code: str) -> None:
    """Switch a special off directly in the database, as a re-seed or another
    worker would: nothing in this process is told."""
    await db.execute(
        update(Special).where(Special.code == code).values(is_active=False)
    )


# --- Which specials run on a day --------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (MONDAY, ["chicken_burger_monday"]),
        (THURSDAY, ["nugget_thursday"]),
        (FRIDAY, ["milkshake_friday"]),
        (SUNDAY, ["veggie_sunday"]),
    ],
)
async def test_each_day_has_its_special(
    db_session: AsyncSession, day: date, expected: list[str]
) -> None:
    assert [
        special.code for special in await load_specials(db_session, day)
    ] == expected


async def test_every_day_of_the_week_has_a_special(db_session: AsyncSession) -> None:
    """The demo should have an answer to "what's today's special?" on any day."""
    for offset in range(7):
        assert await load_specials(db_session, MONDAY + timedelta(days=offset))


async def test_a_special_with_no_weekdays_runs_every_day(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        update(Special).where(Special.code == "milkshake_friday").values(weekdays=[])
    )

    assert [special.code for special in await load_specials(db_session, MONDAY)] == [
        "chicken_burger_monday",
        "milkshake_friday",
    ]


async def test_a_special_on_an_item_that_is_off_the_menu_does_not_run(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        update(MenuItem)
        .where(MenuItem.code == "chicken_burger")
        .values(is_active=False)
    )

    assert await load_specials(db_session, MONDAY) == ()


async def test_a_weekday_outside_one_to_seven_is_refused_by_the_database(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_specials_weekdays_valid"):
        await db_session.execute(update(Special).values(weekdays=[1, 8]))


# --- Pricing ----------------------------------------------------------------


async def test_the_special_comes_off_on_its_day(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)

    cart = await add(db_session, session_id, CHICKEN_BURGER)

    assert cart.discounts == [CHICKEN_SPECIAL]
    assert (cart.subtotal_cents, cart.discount_cents) == (649, 150)
    # Tax on 499.
    assert (cart.tax_cents, cart.total_cents) == (65, 564)


async def test_the_special_does_not_come_off_on_another_day(
    db_session: AsyncSession,
) -> None:
    session_id = await car_on(db_session, FRIDAY)

    cart = await add(db_session, session_id, CHICKEN_BURGER)

    assert cart.discounts == []
    assert cart.total_cents == 649 + 84


async def test_the_weekend_special_comes_off_at_the_weekend(
    db_session: AsyncSession,
) -> None:
    friday = await car_on(db_session, FRIDAY, "lane-1")
    monday = await car_on(db_session, MONDAY, "lane-2")

    at_the_weekend = await add(db_session, friday, MILKSHAKE)
    in_the_week = await add(db_session, monday, MILKSHAKE)

    assert [d.code for d in at_the_weekend.discounts] == ["milkshake_friday"]
    assert at_the_weekend.discount_cents == 100
    assert in_the_week.discounts == []


async def test_a_session_keeps_the_specials_of_the_day_it_opened(
    db_session: AsyncSession,
) -> None:
    """Two cars at the speaker at the same moment, one that arrived just
    before midnight on Monday and one just after. Each is priced by its own day."""
    monday = await car_on(db_session, MONDAY, "lane-1")
    tuesday = await car_on(db_session, MONDAY + timedelta(days=1), "lane-2")

    before_midnight = await add(db_session, monday, CHICKEN_BURGER)
    after_midnight = await add(db_session, tuesday, CHICKEN_BURGER)

    assert before_midnight.discount_cents == 150
    assert after_midnight.discount_cents == 0


async def test_every_unit_gets_the_special(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)

    await add(db_session, session_id, CHICKEN_BURGER, 2)
    cart = await add(db_session, session_id, ("chicken_burger", {"spice": ["spicy"]}))

    assert cart.discounts == [
        CHICKEN_SPECIAL.model_copy(update={"times": 3, "amount_cents": 450})
    ]


async def test_other_items_are_not_discounted(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)

    cart = await add(db_session, session_id, ("cheeseburger", {}))

    assert cart.discounts == []


async def test_removing_the_item_removes_the_discount(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)
    await add(db_session, session_id, WATER)

    cart = await remove_line(db_session, session_id, 1, tax_rate=TAX)

    assert cart.discounts == []
    assert cart.total_cents == 149 + 19


async def test_a_special_and_a_combo_both_apply(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)
    await add(db_session, session_id, SMALL_FRIES)

    cart = await add(db_session, session_id, WATER)

    assert [(d.kind, d.code, d.amount_cents) for d in cart.discounts] == [
        (DiscountKind.COMBO, "burger_combo", 200),
        (DiscountKind.SPECIAL, "chicken_burger_monday", 150),
    ]
    assert (cart.subtotal_cents, cart.discount_cents) == (1047, 350)
    # Tax on 697.
    assert (cart.tax_cents, cart.total_cents) == (91, 788)


async def test_the_special_is_named_in_the_customers_language(
    db_session: AsyncSession,
) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)
    await set_language(db_session, session_id, Language.FR)

    cart = await get_cart(db_session, session_id, tax_rate=TAX)

    assert [d.name for d in cart.discounts] == ["Lundi burger au poulet"]


async def test_a_retired_special_stops_coming_off(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)

    await set_special_active(db_session, "chicken_burger_monday", False)

    assert (await get_cart(db_session, session_id, tax_rate=TAX)).discounts == []


async def test_a_special_brought_back_comes_off_again(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)
    await set_special_active(db_session, "chicken_burger_monday", False)

    await set_special_active(db_session, "chicken_burger_monday", True)

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.discounts == [CHICKEN_SPECIAL]


async def test_switching_an_unknown_special_is_an_error(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(UnknownSpecial) as error:
        await set_special_active(db_session, "free_lunch", False)

    assert error.value.code == "unknown_special"
    assert error.value.details == {"special_code": "free_lunch"}


async def test_two_specials_on_one_item_do_not_stack(db_session: AsyncSession) -> None:
    item_id = (
        await db_session.execute(
            select(MenuItem.id).where(MenuItem.code == "chicken_burger")
        )
    ).scalar_one()
    db_session.add(
        Special(
            code="chicken_again",
            item_id=item_id,
            name_i18n={"en": "Another one"},
            discount_cents=300,
            sort_order=5,
        )
    )
    await db_session.flush()
    session_id = await car_on(db_session, MONDAY)

    cart = await add(db_session, session_id, CHICKEN_BURGER)

    assert cart.discounts == [CHICKEN_SPECIAL]


async def test_a_special_never_takes_off_more_than_the_item_costs(
    db_session: AsyncSession,
) -> None:
    """The price was cut after the special was set up. The burger becomes
    free; it does not become a credit."""
    await db_session.execute(
        update(MenuItem)
        .where(MenuItem.code == "chicken_burger")
        .values(base_price_cents=100)
    )
    session_id = await car_on(db_session, MONDAY)

    cart = await add(db_session, session_id, CHICKEN_BURGER)

    assert (cart.subtotal_cents, cart.discount_cents) == (100, 100)
    assert (cart.tax_cents, cart.total_cents) == (0, 0)


# --- Orders -----------------------------------------------------------------


async def test_an_order_freezes_its_special(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    await add(db_session, session_id, CHICKEN_BURGER)

    placed = await submit_order(db_session, session_id, tax_rate=TAX)
    for special in (await db_session.execute(select(Special))).scalars():
        await db_session.delete(special)
    await db_session.flush()

    assert placed.discounts == [CHICKEN_SPECIAL]
    assert (placed.discount_cents, placed.total_cents) == (150, 564)
    assert await get_order(db_session, placed.order_id) == placed
    row = (await db_session.execute(select(OrderDiscount))).scalar_one()
    assert (row.kind, row.code, row.times) == (
        DiscountKind.SPECIAL,
        "chicken_burger_monday",
        1,
    )


# --- What a customer is told ------------------------------------------------


async def test_mondays_special_as_a_customer_hears_it(db_session: AsyncSession) -> None:
    (special,) = await todays_specials(db_session, MONDAY, Language.EN)

    assert special.model_dump() == {
        "code": "chicken_burger_monday",
        "name": "Chicken burger Monday",
        "description": MONDAY_DESCRIPTION,
        "item_code": "chicken_burger",
        "item_name": "Crispy chicken burger",
        "regular_price_cents": 649,
        "special_price_cents": 499,
        "saves_cents": 150,
        "in_stock": True,
    }


async def test_fridays_special_in_french(db_session: AsyncSession) -> None:
    (special,) = await todays_specials(db_session, FRIDAY, Language.FR)

    assert special.name == "Vendredi lait frappé"
    assert special.item_name == "Lait frappé"
    assert special.description is not None
    assert "tous les vendredis" in special.description
    assert (special.regular_price_cents, special.special_price_cents) == (399, 299)


async def test_no_specials_is_an_empty_list(db_session: AsyncSession) -> None:
    await db_session.execute(update(Special).values(is_active=False))

    assert await todays_specials(db_session, MONDAY, Language.EN) == []


async def test_a_sold_out_special_says_so(db_session: AsyncSession) -> None:
    await set_on_hand(db_session, "chicken_burger", 0)

    (special,) = await todays_specials(db_session, MONDAY, Language.EN)

    assert special.in_stock is False


# --- The week ---------------------------------------------------------------

WEDNESDAY = date(2026, 10, 14)


async def test_the_week_runs_monday_to_sunday_with_today_marked(
    db_session: AsyncSession,
) -> None:
    week = await week_of_specials(db_session, WEDNESDAY, Language.EN)

    assert week.today == WEDNESDAY
    assert [day.date for day in week.days] == [
        MONDAY + timedelta(days=n) for n in range(7)
    ]
    assert [day.weekday for day in week.days] == [1, 2, 3, 4, 5, 6, 7]
    assert [day.day_name for day in week.days] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert [day.is_today for day in week.days] == [
        False,
        False,
        True,
        False,
        False,
        False,
        False,
    ]


async def test_each_day_of_the_week_shows_its_own_special(
    db_session: AsyncSession,
) -> None:
    """So someone watching can check the special the agent names against the
    day it is."""
    week = await week_of_specials(db_session, WEDNESDAY, Language.EN)

    assert [[s.item_code for s in day.specials] for day in week.days] == [
        ["chicken_burger"],
        ["double_cheeseburger"],
        ["onion_rings"],
        ["chicken_nuggets"],
        ["milkshake"],
        ["sundae"],
        ["veggie_burger"],
    ]
    wednesday = week.days[2].specials[0]
    assert (
        wednesday.name,
        wednesday.regular_price_cents,
        wednesday.special_price_cents,
    ) == (
        "Onion ring Wednesday",
        329,
        229,
    )


async def test_a_week_seen_from_sunday_is_the_week_ending_today(
    db_session: AsyncSession,
) -> None:
    week = await week_of_specials(db_session, SUNDAY, Language.EN)

    assert week.days[0].date == SUNDAY - timedelta(days=6)
    assert week.days[-1].date == SUNDAY
    assert week.days[-1].is_today


async def test_the_week_in_french(db_session: AsyncSession) -> None:
    week = await week_of_specials(db_session, WEDNESDAY, Language.FR)

    assert [day.day_name for day in week.days][:3] == ["lundi", "mardi", "mercredi"]
    assert week.days[2].specials[0].name == "Mercredi rondelles d'oignon"


async def test_a_day_with_no_special_has_an_empty_list(
    db_session: AsyncSession,
) -> None:
    await retire(db_session, "onion_ring_wednesday")

    week = await week_of_specials(db_session, WEDNESDAY, Language.EN)

    assert week.days[2].specials == []
    assert week.days[2].is_today


async def test_the_week_shows_a_special_that_has_sold_out(
    db_session: AsyncSession,
) -> None:
    await set_on_hand(db_session, "onion_rings", 0)

    week = await week_of_specials(db_session, WEDNESDAY, Language.EN)

    assert week.days[2].specials[0].in_stock is False
    assert week.days[3].specials[0].in_stock is True


# --- The cache --------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(clock: Clock) -> SpecialsCache:
    return TtlCache(timedelta(seconds=60), clock=clock)


async def codes(db: AsyncSession, day: date, cache: SpecialsCache | None) -> list[str]:
    return [s.code for s in await todays_specials(db, day, Language.EN, cache=cache)]


async def test_a_second_question_is_answered_from_the_cache(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    """Shown by changing the database behind the cache's back: the cached
    answer is still given, which it could not be if the database were read."""
    first = await codes(db_session, MONDAY, cache)
    await retire(db_session, "chicken_burger_monday")

    second = await codes(db_session, MONDAY, cache)

    assert first == second == ["chicken_burger_monday"]


async def test_without_a_cache_every_question_reads_the_database(
    db_session: AsyncSession,
) -> None:
    await codes(db_session, MONDAY, None)
    await retire(db_session, "chicken_burger_monday")

    assert await codes(db_session, MONDAY, None) == []


async def test_the_cache_is_read_again_once_its_time_is_up(
    db_session: AsyncSession, cache: SpecialsCache, clock: Clock
) -> None:
    await codes(db_session, MONDAY, cache)
    await retire(db_session, "chicken_burger_monday")

    clock.now += 59
    still_cached = await codes(db_session, MONDAY, cache)
    clock.now += 1
    refreshed = await codes(db_session, MONDAY, cache)

    assert still_cached == ["chicken_burger_monday"]
    assert refreshed == []


async def test_invalidating_makes_the_next_question_read_the_database(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    await codes(db_session, MONDAY, cache)
    await retire(db_session, "chicken_burger_monday")

    cache.invalidate()

    assert await codes(db_session, MONDAY, cache) == []


async def test_each_day_is_cached_on_its_own(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    """Asking on Monday must never answer Friday's question."""
    assert await codes(db_session, MONDAY, cache) == ["chicken_burger_monday"]
    assert await codes(db_session, FRIDAY, cache) == ["milkshake_friday"]
    assert await codes(db_session, MONDAY, cache) == ["chicken_burger_monday"]


async def test_no_specials_today_is_cached_too(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    await retire(db_session, "chicken_burger_monday")
    assert await codes(db_session, MONDAY, cache) == []

    await db_session.execute(update(Special).values(is_active=True))

    assert await codes(db_session, MONDAY, cache) == []


async def test_stock_is_read_live_even_when_the_list_is_cached(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    """The list rarely changes; stock changes with every order."""
    (before,) = await todays_specials(db_session, MONDAY, Language.EN, cache=cache)

    await set_on_hand(db_session, "chicken_burger", 0)
    (sold_out,) = await todays_specials(db_session, MONDAY, Language.EN, cache=cache)
    await set_on_hand(db_session, "chicken_burger", 5)
    (restocked,) = await todays_specials(db_session, MONDAY, Language.EN, cache=cache)

    assert (before.in_stock, sold_out.in_stock, restocked.in_stock) == (
        True,
        False,
        True,
    )


async def test_one_cached_list_serves_every_language(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    (english,) = await todays_specials(db_session, MONDAY, Language.EN, cache=cache)
    await retire(db_session, "chicken_burger_monday")

    (french,) = await todays_specials(db_session, MONDAY, Language.FR, cache=cache)

    assert (english.name, french.name) == (
        "Chicken burger Monday",
        "Lundi burger au poulet",
    )


async def test_a_stale_cache_never_changes_what_a_cart_is_charged(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    """The cache can make the agent mention a special that has just ended.
    It cannot make a cart charge for one: pricing reads the database."""
    await codes(db_session, MONDAY, cache)
    await retire(db_session, "chicken_burger_monday")
    session_id = await car_on(db_session, MONDAY)

    cart = await add(db_session, session_id, CHICKEN_BURGER)

    assert await codes(db_session, MONDAY, cache) == ["chicken_burger_monday"]
    assert cart.discounts == []
    assert cart.total_cents == 649 + 84


# --- The agent's tool -------------------------------------------------------


def tool_context(
    db: AsyncSession, session_id: uuid.UUID, cache: SpecialsCache | None = None
) -> ToolContext:
    return ToolContext(db, session_id, TAX, LIMITS, specials_cache=cache)


async def test_the_tool_gives_the_special_and_the_combos(
    db_session: AsyncSession,
) -> None:
    session_id = await car_on(db_session, MONDAY)

    result = await run_tool(
        tool_context(db_session, session_id), "get_todays_specials", {}
    )

    (special,) = result["specials"]
    assert (special["item_code"], special["special_price_cents"]) == (
        "chicken_burger",
        499,
    )
    assert [combo["code"] for combo in result["combos"]] == ["burger_combo"]
    assert result["message"].startswith("Tell the customer today's special")
    assert "applied for you" in result["message"]
    # Asked about the special, the agent answers about the special. Offering
    # the combo too made "two of those" ambiguous on a real model.
    assert "Mention the combo only if they asked" in result["message"]


async def test_the_tool_answers_for_the_sessions_own_day(
    db_session: AsyncSession,
) -> None:
    monday = await car_on(db_session, MONDAY, "lane-1")
    friday = await car_on(db_session, FRIDAY, "lane-2")

    async def special_for(session_id: uuid.UUID) -> str:
        result = await run_tool(
            tool_context(db_session, session_id), "get_todays_specials", {}
        )
        return result["specials"][0]["code"]

    assert await special_for(monday) == "chicken_burger_monday"
    assert await special_for(friday) == "milkshake_friday"


async def test_the_tool_follows_the_session_language(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, FRIDAY)
    context = tool_context(db_session, session_id)
    await run_tool(context, "set_language", {"language": "fr"})

    result = await run_tool(context, "get_todays_specials", {})

    assert result["specials"][0]["name"] == "Vendredi lait frappé"
    assert result["combos"][0]["name"] == "Trio burger"


async def test_the_tool_uses_the_cache_it_is_given(
    db_session: AsyncSession, cache: SpecialsCache
) -> None:
    session_id = await car_on(db_session, MONDAY)
    context = tool_context(db_session, session_id, cache)
    await run_tool(context, "get_todays_specials", {})
    await retire(db_session, "chicken_burger_monday")

    cached = await run_tool(context, "get_todays_specials", {})
    fresh = await run_tool(
        tool_context(db_session, session_id), "get_todays_specials", {}
    )

    assert [s["code"] for s in cached["specials"]] == ["chicken_burger_monday"]
    assert fresh["specials"] == []
    assert fresh["message"].startswith("There is no special today.")


async def test_the_tool_takes_no_arguments(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)

    result = await run_tool(
        tool_context(db_session, session_id), "get_todays_specials", {"day": "friday"}
    )

    assert result["error"]["code"] == "invalid_tool_call"


async def test_asking_about_specials_changes_nothing(db_session: AsyncSession) -> None:
    session_id = await car_on(db_session, MONDAY)
    before = await get_cart(db_session, session_id, tax_rate=TAX)

    await run_tool(tool_context(db_session, session_id), "get_todays_specials", {})

    assert await get_cart(db_session, session_id, tax_rate=TAX) == before


# --- Over HTTP --------------------------------------------------------------


@pytest.fixture
def today(app: FastAPI) -> dict[str, date]:
    """Lets a test say what day it is for the API. Starts on Monday."""
    calendar = {"today": MONDAY}
    app.dependency_overrides[get_business_date] = lambda: calendar["today"]
    return calendar


pytest_today = pytest.mark.usefixtures("today")


async def read_specials(
    client: httpx.AsyncClient, **params: str
) -> list[dict[str, Any]]:
    response = await client.get("/specials", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest_today
async def test_todays_specials_are_listed(client: httpx.AsyncClient) -> None:
    (special,) = await read_specials(client)

    assert special == {
        "code": "chicken_burger_monday",
        "name": "Chicken burger Monday",
        "description": MONDAY_DESCRIPTION,
        "item_code": "chicken_burger",
        "item_name": "Crispy chicken burger",
        "regular_price_cents": 649,
        "special_price_cents": 499,
        "saves_cents": 150,
        "in_stock": True,
    }


async def test_the_list_follows_the_day(
    client: httpx.AsyncClient, today: dict[str, date]
) -> None:
    monday = await read_specials(client)
    today["today"] = FRIDAY
    friday = await read_specials(client, language="fr")

    assert [s["code"] for s in monday] == ["chicken_burger_monday"]
    assert [s["name"] for s in friday] == ["Vendredi lait frappé"]


@pytest_today
async def test_the_endpoint_answers_from_the_cache(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await read_specials(client)
    await retire(db_session, "chicken_burger_monday")
    await db_session.commit()

    assert [s["code"] for s in await read_specials(client)] == ["chicken_burger_monday"]


@pytest_today
async def test_ending_a_special_shows_on_the_very_next_read(
    client: httpx.AsyncClient,
) -> None:
    """A change made through the API clears the cache, so nobody waits a
    minute to stop hearing about a special that has ended."""
    await read_specials(client)

    response = await client.put(
        "/specials/chicken_burger_monday", json={"is_active": False}
    )

    assert response.status_code == 200
    assert response.json() == {"code": "chicken_burger_monday", "is_active": False}
    assert await read_specials(client) == []


@pytest_today
async def test_a_special_can_be_brought_back(client: httpx.AsyncClient) -> None:
    await client.put("/specials/chicken_burger_monday", json={"is_active": False})
    await read_specials(client)

    await client.put("/specials/chicken_burger_monday", json={"is_active": True})

    assert [s["code"] for s in await read_specials(client)] == ["chicken_burger_monday"]


@pytest_today
async def test_changing_an_unknown_special_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put("/specials/free_lunch", json={"is_active": False})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_special"


@pytest_today
@pytest.mark.parametrize(
    "body", [{}, {"is_active": "sometimes"}, {"is_active": True, "discount_cents": 600}]
)
async def test_a_bad_change_is_rejected(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    """In particular, the amount of a special cannot be set through the API."""
    response = await client.put("/specials/chicken_burger_monday", json=body)

    assert response.status_code == 422
    assert (await read_specials(client))[0]["saves_cents"] == 150


async def test_a_session_opened_over_http_keeps_the_days_date(
    client: httpx.AsyncClient, today: dict[str, date]
) -> None:
    opened = (await client.post("/sessions")).json()
    today["today"] = FRIDAY

    read_back = (await client.get(f"/sessions/{opened['session_id']}")).json()

    assert opened["business_date"] == read_back["business_date"] == "2026-10-12"


async def test_a_cart_is_priced_by_the_day_its_session_opened(
    client: httpx.AsyncClient, today: dict[str, date]
) -> None:
    """Opened on Monday night; the order is finished after midnight."""
    today["today"] = MONDAY
    session_id = (await client.post("/sessions")).json()["session_id"]
    today["today"] = MONDAY + timedelta(days=1)

    cart = (
        await client.post(
            f"/sessions/{session_id}/cart/lines",
            json={"item_code": "chicken_burger", "selections": {"spice": ["mild"]}},
        )
    ).json()

    assert cart["discounts"] == [CHICKEN_SPECIAL.model_dump(mode="json")]
    assert cart["total_cents"] == 564


async def test_the_week_endpoint(
    client: httpx.AsyncClient, today: dict[str, date]
) -> None:
    today["today"] = WEDNESDAY

    response = await client.get("/specials/week", params={"language": "fr"})

    assert response.status_code == 200
    week = response.json()
    assert week["today"] == "2026-10-14"
    assert [day["is_today"] for day in week["days"]].index(True) == 2
    assert week["days"][2]["day_name"] == "mercredi"
    assert week["days"][2]["specials"][0]["item_code"] == "onion_rings"
