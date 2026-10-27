"""Remembering phone callers: their number, their last orders, and reordering.
Real menu, real database."""

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.tools import ToolContext, run_tool
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import Caller, CustomerSession
from drivetruagent.enums import Channel
from drivetruagent.ordering.callers import forget_history, past_orders
from drivetruagent.ordering.cart import add_item, request_confirmation
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.orders import submit_order
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits
from tests.integration.conftest import TEST_TAX_RATE

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
REGULAR = "+14165550123"
SOMEONE_ELSE = "+16475550199"

type Line = tuple[str, int, dict[str, list[str]]]


async def place(
    db: AsyncSession, phone: str | None, lines: list[Line], on: date
) -> int:
    """A phone order from start to finish. Returns its order number."""
    session_id = (
        await open_session(
            db, business_date=on, channel=Channel.PHONE, caller_phone=phone
        )
    ).id
    for code, quantity, selections in lines:
        await add_item(db, session_id, code, quantity, selections, tax_rate=TAX)
    await request_confirmation(db, session_id, tax_rate=TAX)
    order = await submit_order(db, session_id, tax_rate=TAX)
    await db.commit()
    return order.order_number


async def call(db: AsyncSession, phone: str | None) -> ToolContext:
    session_id = (await open_session(db, channel=Channel.PHONE, caller_phone=phone)).id
    await db.commit()
    return ToolContext(db, session_id, TAX, LIMITS)


async def caller(db: AsyncSession, phone: str) -> Caller:
    return (
        await db.execute(
            select(Caller)
            .where(Caller.phone_e164 == phone)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


CHEESEBURGER_COMBO: list[Line] = [
    ("cheeseburger", 1, {"extras": ["bacon"]}),
    ("fries", 1, {"size": ["large"]}),
    ("cola", 1, {"size": ["large"]}),
]


# --- Opening a call ---------------------------------------------------------


async def test_a_first_call_creates_the_caller_and_links_the_session(
    db_session: AsyncSession,
) -> None:
    session = await open_session(
        db_session, channel=Channel.PHONE, caller_phone=REGULAR
    )

    found = await caller(db_session, REGULAR)
    assert (found.call_count, found.history_opted_out) == (1, False)
    assert session.caller_id == found.id
    assert session.lane.startswith("phone-")


async def test_every_call_from_the_number_is_counted(db_session: AsyncSession) -> None:
    sessions = [
        await open_session(db_session, channel=Channel.PHONE, caller_phone=REGULAR)
        for _ in range(3)
    ]

    found = await caller(db_session, REGULAR)
    assert found.call_count == 3
    assert found.last_call_at >= found.first_call_at
    assert {session.caller_id for session in sessions} == {found.id}


async def test_calls_never_wait_for_each_other(db_session: AsyncSession) -> None:
    """Unlike a drive-through lane, any number of calls can be open at once."""
    first = await open_session(db_session, channel=Channel.PHONE, caller_phone=REGULAR)
    second = await open_session(db_session, channel=Channel.PHONE, caller_phone=REGULAR)
    withheld = await open_session(db_session, channel=Channel.PHONE)

    assert len({first.lane, second.lane, withheld.lane}) == 3
    assert withheld.caller_id is None


async def test_a_drive_through_car_has_no_caller(db_session: AsyncSession) -> None:
    session = await open_session(db_session)

    assert (session.channel, session.caller_id, session.lane) == (
        Channel.DRIVE_THRU,
        None,
        "lane-1",
    )
    with pytest.raises(ValueError, match="phone"):
        await open_session(db_session, "lane-2", caller_phone=REGULAR)


async def test_deleting_a_caller_keeps_their_orders(
    db_session: AsyncSession, menu: None
) -> None:
    await place(db_session, REGULAR, [("cookie", 1, {})], date(2026, 10, 20))

    await db_session.delete(await caller(db_session, REGULAR))
    await db_session.flush()

    sessions = (await db_session.execute(select(CustomerSession))).scalars().all()
    assert [s.caller_id for s in sessions] == [None]


# --- Past orders ------------------------------------------------------------


async def test_the_last_five_orders_come_back_newest_first(
    db_session: AsyncSession, menu: None
) -> None:
    numbers = [
        await place(db_session, REGULAR, [("cookie", day, {})], date(2026, 10, day))
        for day in range(1, 7)
    ]
    context = await call(db_session, REGULAR)

    found = await past_orders(db_session, context.session_id)

    assert [order.order_number for order in found] == numbers[:0:-1]
    assert found[0].ordered_on == date(2026, 10, 6)
    assert found[0].lines[0].quantity == 6


async def test_a_past_order_keeps_its_choices(
    db_session: AsyncSession, menu: None
) -> None:
    await place(db_session, REGULAR, CHEESEBURGER_COMBO, date(2026, 10, 20))
    context = await call(db_session, REGULAR)

    (order,) = await past_orders(db_session, context.session_id)

    assert [
        (line.item_code, [option.option_code for option in line.options])
        for line in order.lines
    ] == [
        ("cheeseburger", ["bacon"]),
        ("fries", ["large", "regular", "plain"]),
        ("cola", ["large"]),
    ]


async def test_only_the_callers_own_orders_come_back(
    db_session: AsyncSession, menu: None
) -> None:
    await place(db_session, SOMEONE_ELSE, [("cookie", 1, {})], date(2026, 10, 20))
    await place(db_session, None, [("cookie", 2, {})], date(2026, 10, 21))
    mine = await place(db_session, REGULAR, [("apple_pie", 1, {})], date(2026, 10, 22))
    context = await call(db_session, REGULAR)

    found = await past_orders(db_session, context.session_id)

    assert [order.order_number for order in found] == [mine]


@pytest.mark.parametrize("phone", [None, "drive-through"])
async def test_a_withheld_number_or_a_car_has_no_past_orders(
    db_session: AsyncSession, menu: None, phone: str | None
) -> None:
    await place(db_session, None, [("cookie", 1, {})], date(2026, 10, 20))
    session = (
        await open_session(db_session)
        if phone
        else await open_session(db_session, channel=Channel.PHONE)
    )

    assert await past_orders(db_session, session.id) == []


async def test_after_opting_out_nothing_is_read_back_but_the_number_stays(
    db_session: AsyncSession, menu: None
) -> None:
    await place(db_session, REGULAR, [("cookie", 1, {})], date(2026, 10, 20))
    context = await call(db_session, REGULAR)

    assert await forget_history(db_session, context.session_id) is True
    later = await call(db_session, REGULAR)

    assert await past_orders(db_session, later.session_id) == []
    found = await caller(db_session, REGULAR)
    assert (found.history_opted_out, found.call_count) == (True, 3)


# --- The tools --------------------------------------------------------------


async def test_get_past_orders_gives_the_date_and_items_but_no_totals(
    db_session: AsyncSession, menu: None
) -> None:
    number = await place(db_session, REGULAR, CHEESEBURGER_COMBO, date(2026, 10, 20))
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "get_past_orders", {})

    (order,) = result["orders"]
    assert (order["order_number"], order["ordered_on"]) == (number, "2026-10-20")
    assert "total_cents" not in order
    assert "today" in result
    assert "never say a past total" in result["message"]


async def test_get_past_orders_with_nothing_to_read_back_says_so(
    db_session: AsyncSession, menu: None
) -> None:
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "get_past_orders", {})

    assert result["orders"] == []
    assert "no past orders" in result["message"]


async def test_reorder_adds_every_line_at_todays_prices(
    db_session: AsyncSession, menu: None
) -> None:
    number = await place(db_session, REGULAR, CHEESEBURGER_COMBO, date(2026, 10, 20))
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "reorder", {"order_number": number})

    assert [line["decision"] for line in result["lines"]] == ["allow"] * 3
    cart = result["cart"]
    assert [
        (line["item_code"], [o["option_code"] for o in line["options"]])
        for line in cart["lines"]
    ] == [
        ("cheeseburger", ["bacon"]),
        ("fries", ["large", "regular", "plain"]),
        ("cola", ["large"]),
    ]
    assert [d["code"] for d in cart["discounts"]] == ["burger_combo"]


async def test_reorder_leaves_out_what_is_sold_out_and_says_so(
    db_session: AsyncSession, menu: None
) -> None:
    number = await place(db_session, REGULAR, CHEESEBURGER_COMBO, date(2026, 10, 20))
    await set_on_hand(db_session, "fries", 0)
    await db_session.commit()
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "reorder", {"order_number": number})

    assert [(line["item_name"], line["decision"]) for line in result["lines"]] == [
        ("Cheeseburger", "allow"),
        ("Fries", "reject"),
        ("Cola", "allow"),
    ]
    assert result["lines"][1]["rule"] == "insufficient_stock"
    assert [line["item_code"] for line in result["cart"]["lines"]] == [
        "cheeseburger",
        "cola",
    ]


async def test_reorder_cannot_reach_someone_elses_order(
    db_session: AsyncSession, menu: None
) -> None:
    theirs = await place(
        db_session, SOMEONE_ELSE, [("cookie", 1, {})], date(2026, 10, 20)
    )
    mine = await place(db_session, REGULAR, [("apple_pie", 1, {})], date(2026, 10, 21))
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "reorder", {"order_number": theirs})

    assert result["error"]["code"] == "unknown_past_order"
    assert result["error"]["details"]["recent_order_numbers"] == [mine]


async def test_forget_my_orders_stops_the_read_back(
    db_session: AsyncSession, menu: None
) -> None:
    await place(db_session, REGULAR, [("cookie", 1, {})], date(2026, 10, 20))
    context = await call(db_session, REGULAR)

    result = await run_tool(context, "forget_my_orders", {})
    after = await run_tool(context, "get_past_orders", {})

    assert "won't be kept" in result["message"]
    assert "Do not say their number is deleted" in result["message"]
    assert after["orders"] == []


async def test_forget_my_orders_on_a_withheld_number_has_nothing_to_stop(
    db_session: AsyncSession, menu: None
) -> None:
    context = await call(db_session, None)

    result = await run_tool(context, "forget_my_orders", {})

    assert "nothing is being kept" in result["message"]


# --- The API ----------------------------------------------------------------


def allow_typed_caller_id(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        tax_rate=TEST_TAX_RATE,
        openai_api_key=None,
        allow_typed_caller_id=True,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )


async def test_a_typed_caller_number_is_refused_by_default(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/sessions", json={"channel": "phone", "caller_phone": "416 555 0123"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "typed_caller_id_off"


async def test_a_typed_caller_number_opens_a_call_when_allowed(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    allow_typed_caller_id(app)

    response = await client.post(
        "/sessions", json={"channel": "phone", "caller_phone": "(416) 555-0123"}
    )

    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    assert body["channel"] == "phone"
    assert "caller_phone" not in body
    assert (await caller(db_session, REGULAR)).call_count == 1


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"channel": "phone", "caller_phone": "555"}, 422),
        ({"channel": "drive_thru", "caller_phone": "416 555 0123"}, 422),
    ],
)
async def test_a_bad_caller_number_is_refused(
    app: FastAPI, client: httpx.AsyncClient, body: dict[str, str], status: int
) -> None:
    allow_typed_caller_id(app)

    response = await client.post("/sessions", json=body)

    assert response.status_code == status


async def test_a_drive_through_session_still_opens_on_lane_one(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/sessions")

    assert (response.json()["lane"], response.json()["channel"]) == (
        "lane-1",
        "drive_thru",
    )
