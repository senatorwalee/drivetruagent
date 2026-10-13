"""Handoffs against a real database and the real menu.

A session is handed to a person in three ways: a very large order that the
customer confirmed, a request for a person, and the three-minute limit. Each
must leave exactly one handoff, and the package built from it must let the
person carry on without asking the customer to start again.

Cookies are 149 each. Tax is 13%.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import timekeeper
from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.scripted import ScriptedModel, call, say
from drivetruagent.db.models import CustomerSession, Handoff, Order, Reservation
from drivetruagent.enums import (
    HandoffOutcome,
    HandoffStatus,
    ReservationStatus,
    SessionState,
)
from drivetruagent.ordering import sessions
from drivetruagent.ordering.cart import add_item
from drivetruagent.ordering.errors import (
    EmptyCart,
    InsufficientStock,
    SessionNotHandedOff,
)
from drivetruagent.ordering.inventory import set_on_hand, stock_levels
from drivetruagent.ordering.orders import place_handed_off_order, submit_order
from drivetruagent.ordering.sessions import end_session, get_session, open_session
from drivetruagent.ordering.timers import TimerSettings
from drivetruagent.policy import gateway, handoffs
from drivetruagent.policy.actions import AddItem, UpdateLine
from drivetruagent.policy.decisions import Decision, PolicyLimits
from drivetruagent.policy.errors import HandoffNotFound, HandoffResolved
from drivetruagent.policy.handoff_package import HandoffPackage

TAX = Decimal("0.13")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TIMERS = TimerSettings()
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
AGENT = AgentConfig(
    restaurant_name="Maple Lane Burgers", tax_rate=TAX, limits=LIMITS, max_tool_rounds=4
)

pytestmark = pytest.mark.usefixtures("menu")


async def open_car(db: AsyncSession, lane: str = "lane-1") -> uuid.UUID:
    session_id = (await open_session(db, lane)).id
    await db.commit()
    return session_id


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    return await open_car(db_session)


async def ask_for_a_person(
    db: AsyncSession, session_id: uuid.UUID, reason: str = "wants to pay by cheque"
) -> None:
    outcome = await gateway.hand_to_human(db, session_id, reason, tax_rate=TAX)
    assert outcome.decision is Decision.ESCALATE


async def two_cookies_then_a_person(
    db: AsyncSession, session_id: uuid.UUID, cookies_on_hand: int = 5
) -> Handoff:
    """The usual starting point: two cookies in the cart, then handed off."""
    await set_on_hand(db, "cookie", cookies_on_hand)
    await add_item(db, session_id, "cookie", 2, tax_rate=TAX)
    await ask_for_a_person(db, session_id)
    return await the_handoff(db)


async def the_handoff(db: AsyncSession) -> Handoff:
    return (await db.execute(select(Handoff))).scalar_one()


async def handoff_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Handoff))).scalar_one()


async def package(db: AsyncSession, handoff: Handoff) -> HandoffPackage:
    return await handoffs.get_package(db, handoff.id, tax_rate=TAX, now=NOW)


async def cookies(db: AsyncSession) -> tuple[int, int]:
    """(on hand, reserved) for cookies."""
    level = next(lv for lv in await stock_levels(db) if lv.item_code == "cookie")
    return level.on_hand, level.reserved


# --- When a handoff is created ----------------------------------------------


async def test_asking_for_a_person_leaves_a_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await ask_for_a_person(db_session, session_id, "wants to pay by cheque")

    handoff = await the_handoff(db_session)
    assert handoff.session_id == session_id
    assert handoff.reason == "human_requested"
    assert handoff.details == {"reason": "wants to pay by cheque"}
    assert handoff.action is None
    assert handoff.status is HandoffStatus.OPEN
    assert (handoff.acknowledged_at, handoff.resolved_at, handoff.outcome) == (
        None,
        None,
        None,
    )


async def test_a_confirmed_very_large_order_leaves_a_handoff_with_what_was_asked(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    a_thousand = AddItem(
        item_code="fries", quantity=1000, selections={"size": ["large"]}
    )
    held = await gateway.execute(
        db_session, session_id, a_thousand, tax_rate=TAX, limits=LIMITS
    )
    assert held.pending_action_id is not None
    assert await handoff_count(db_session) == 0

    outcome = await gateway.confirm(
        db_session, session_id, held.pending_action_id, tax_rate=TAX, limits=LIMITS
    )

    assert outcome.decision is Decision.ESCALATE
    handoff = await the_handoff(db_session)
    assert handoff.reason == "extreme_quantity"
    assert handoff.details == {"line_quantity": 1000, "cart_item_count": 1000}
    assert handoff.action == {
        "type": "add_item",
        "item_code": "fries",
        "quantity": 1000,
        "selections": {"size": ["large"]},
    }


async def test_the_three_minute_limit_leaves_a_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    customer_session = await get_session(db_session, session_id)
    three_minutes_in = customer_session.started_at + timedelta(seconds=180)
    await sessions.begin_turn(db_session, session_id, now=three_minutes_in)
    await sessions.end_turn(db_session, session_id, now=three_minutes_in)

    await timekeeper.enforce(
        db_session, session_id, now=three_minutes_in, timers=TIMERS, tax_rate=TAX
    )

    handoff = await the_handoff(db_session)
    assert (handoff.reason, handoff.details, handoff.action) == ("time_limit", {}, None)


async def test_a_handoff_through_the_agent_is_recorded_too(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("request_human", reason="customer asked for a manager"),
            say("A team member will be right with you."),
        ]
    )

    await run_turn(
        db_session, session_id, "Get me a manager", model=model, config=AGENT
    )

    handoff = await the_handoff(db_session)
    assert handoff.details == {"reason": "customer asked for a manager"}


async def test_sessions_that_end_any_other_way_leave_no_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    abandoned = await open_car(db_session, "lane-2")
    terminated = await open_car(db_session, "lane-3")
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    await submit_order(db_session, session_id, tax_rate=TAX)
    await gateway.abandon(db_session, abandoned, tax_rate=TAX)
    await end_session(db_session, terminated, SessionState.TERMINATED, "abuse")

    assert await handoff_count(db_session) == 0


async def test_a_refused_request_for_a_person_leaves_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The session had already ended, so nothing was handed off."""
    await end_session(db_session, session_id, SessionState.ABANDONED, "test")

    outcome = await gateway.hand_to_human(
        db_session, session_id, "hello?", tax_rate=TAX
    )

    assert outcome.decision is Decision.REJECT
    assert await handoff_count(db_session) == 0


async def test_a_session_is_handed_off_once(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await ask_for_a_person(db_session, session_id)

    again = await gateway.hand_to_human(db_session, session_id, "again", tax_rate=TAX)

    assert again.decision is Decision.REJECT
    assert await handoff_count(db_session) == 1
    with pytest.raises(IntegrityError, match="uq_handoffs_session_id"):
        await handoffs.open_handoff(db_session, session_id, "time_limit", {})


# --- What the database refuses ----------------------------------------------


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"status": "resolved"}, "ck_handoffs_"),
        ({"resolved_at": NOW}, "ck_handoffs_resolved_at_matches_status"),
        ({"outcome": "cancelled"}, "ck_handoffs_outcome_matches_status"),
        ({"status": "lost"}, "ck_handoffs_status_valid"),
    ],
)
async def test_a_handoff_in_an_impossible_state_is_refused(
    db_session: AsyncSession,
    session_id: uuid.UUID,
    values: dict[str, Any],
    constraint: str,
) -> None:
    await ask_for_a_person(db_session, session_id)

    with pytest.raises(IntegrityError, match=constraint):
        await db_session.execute(update(Handoff).values(**values))


# --- The package ------------------------------------------------------------


async def test_the_package_says_who_why_and_what_to_do(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)

    shown = await package(db_session, handoff)

    assert (shown.handoff_id, shown.session_id) == (handoff.id, session_id)
    assert (shown.lane, shown.language.value) == ("lane-1", "en")
    assert shown.status is HandoffStatus.OPEN
    assert (shown.reason.code, shown.reason.title) == (
        "human_requested",
        "A person was asked for",
    )
    assert shown.details == {"reason": "wants to pay by cheque"}
    assert shown.outstanding == (
        'A person was asked for. The reason given: "wants to pay by cheque". '
        "Carry on from where the conversation stopped. The cart holds 2 items "
        "and can be placed as it stands."
    )
    assert shown.session_ended_at is not None
    assert shown.order is None


async def test_the_package_carries_the_cart_as_the_customer_left_it(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "classic_burger", tax_rate=TAX)
    await add_item(
        db_session, session_id, "fries", 1, {"size": ["small"]}, tax_rate=TAX
    )
    await add_item(db_session, session_id, "bottled_water", tax_rate=TAX)
    await ask_for_a_person(db_session, session_id)

    shown = await package(db_session, await the_handoff(db_session))

    assert [(line.item_code, line.quantity) for line in shown.cart.lines] == [
        ("classic_burger", 1),
        ("fries", 1),
        ("bottled_water", 1),
    ]
    # The combo the customer had earned is still theirs.
    assert shown.cart.discount_cents == 200
    assert (shown.item_count, shown.total_cents) == (3, 844)
    assert shown.cart.state is SessionState.HANDED_OFF


async def test_the_package_shows_the_stock_being_held(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    expires_at = (await db_session.execute(select(Reservation.expires_at))).scalar_one()

    (held,) = (await package(db_session, handoff)).stock

    assert (held.item_code, held.item_name, held.quantity) == (
        "cookie",
        "Chocolate chip cookie",
        2,
    )
    assert (held.status, held.expires_at, held.held) == (
        ReservationStatus.ACTIVE,
        expires_at,
        True,
    )


async def test_a_hold_that_has_run_out_is_shown_as_no_longer_held(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The person should know the stock may have gone before they promise it."""
    handoff = await two_cookies_then_a_person(db_session, session_id)
    await db_session.execute(
        update(Reservation).values(expires_at=NOW - timedelta(minutes=1))
    )

    (held,) = (await package(db_session, handoff)).stock

    assert (held.status, held.held) == (ReservationStatus.ACTIVE, False)


async def test_the_package_for_a_very_large_order_names_the_item(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await gateway.execute(
        db_session,
        session_id,
        AddItem(item_code="cookie", quantity=400),
        tax_rate=TAX,
        limits=LIMITS,
    )
    await gateway.confirm(
        db_session,
        session_id,
        held.pending_action_id,  # pyright: ignore[reportArgumentType]
        tax_rate=TAX,
        limits=LIMITS,
    )

    shown = await package(db_session, await the_handoff(db_session))

    assert shown.reason.title == "Very large order"
    assert shown.outstanding.startswith(
        "The customer asked for 400 of Chocolate chip cookie and confirmed it."
    )
    assert shown.outstanding.endswith("The cart is empty.")
    assert shown.cart.lines == []
    assert shown.stock == []


async def test_a_very_large_change_to_a_line_is_described_by_its_line(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "cookie", 2, tax_rate=TAX)
    held = await gateway.execute(
        db_session,
        session_id,
        UpdateLine(line_no=1, quantity=90),
        tax_rate=TAX,
        limits=LIMITS,
    )
    await gateway.confirm(
        db_session,
        session_id,
        held.pending_action_id,  # pyright: ignore[reportArgumentType]
        tax_rate=TAX,
        limits=LIMITS,
    )

    shown = await package(db_session, await the_handoff(db_session))

    assert shown.outstanding.startswith(
        "The customer asked to change line L1 to 90 and confirmed it."
    )
    # The change was not made: the cart is what it was before.
    assert [line.quantity for line in shown.cart.lines] == [2]


async def test_the_package_includes_the_conversation_and_what_the_backend_decided(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("add_item", item_code="cookie", quantity=2, selections=[]),
            say("Two cookies. Anything else?"),
            call("request_human", reason="customer asked for a manager"),
            say("A team member will be right with you."),
        ]
    )
    await run_turn(db_session, session_id, "Two cookies", model=model, config=AGENT)
    await run_turn(db_session, session_id, "A manager", model=model, config=AGENT)

    shown = await package(db_session, await the_handoff(db_session))

    assert [(line.speaker, line.text) for line in shown.transcript] == [
        ("customer", "Two cookies"),
        ("agent", "Two cookies. Anything else?"),
        ("customer", "A manager"),
        ("agent", "A team member will be right with you."),
    ]
    assert [(step.tool, step.decision, step.rule) for step in shown.steps] == [
        ("add_item", "allow", None),
        ("request_human", "escalate", "human_requested"),
    ]
    assert shown.steps[0].arguments["item_code"] == "cookie"
    assert (shown.earlier_lines, shown.earlier_steps) == (0, 0)


async def test_the_package_includes_what_was_said_after_the_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Why the package is built when read, not saved at the moment of the
    handoff: the agent's goodbye is recorded just after."""
    model = ScriptedModel(
        [call("request_human", reason="asked"), say("Someone is on their way.")]
    )

    await run_turn(db_session, session_id, "A person please", model=model, config=AGENT)

    shown = await package(db_session, await the_handoff(db_session))
    assert shown.transcript[-1].text == "Someone is on their way."
    assert shown.steps[-1].tool == "request_human"


async def test_a_timed_out_session_shows_the_timer_and_its_last_words(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    customer_session = await get_session(db_session, session_id)
    three_minutes_in = customer_session.started_at + timedelta(seconds=180)
    await sessions.end_turn(db_session, session_id, now=three_minutes_in)

    await timekeeper.enforce(
        db_session, session_id, now=three_minutes_in, timers=TIMERS, tax_rate=TAX
    )

    shown = await package(db_session, await the_handoff(db_session))
    assert shown.reason.title == "Took longer than the time limit"
    assert [(step.tool, step.rule, step.message) for step in shown.steps] == [
        ("timer", "time_limit", "hand off")
    ]
    assert [line.text for line in shown.transcript] == [
        "A team member will help you finish your order. One moment, please."
    ]


async def test_an_unknown_handoff_is_an_error(db_session: AsyncSession) -> None:
    with pytest.raises(HandoffNotFound) as error:
        await handoffs.get_package(db_session, uuid.uuid4(), tax_rate=TAX, now=NOW)

    assert error.value.code == "handoff_not_found"


# --- The queue --------------------------------------------------------------


async def test_the_queue_lists_the_longest_waiting_first(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    second = await open_car(db_session, "lane-2")
    await add_item(db_session, session_id, "cookie", 2, tax_rate=TAX)
    await ask_for_a_person(db_session, session_id)
    await ask_for_a_person(db_session, second)
    # Both were created in one test transaction, so they share a timestamp.
    # Spread them, so the order below is decided by time and not by chance.
    await db_session.execute(
        update(Handoff)
        .where(Handoff.session_id == second)
        .values(created_at=Handoff.created_at + timedelta(minutes=1))
    )

    queue = await handoffs.list_handoffs(db_session, tax_rate=TAX)

    assert [entry.session_id for entry in queue] == [session_id, second]
    first = queue[0]
    assert (first.lane, first.status, first.reason.title) == (
        "lane-1",
        HandoffStatus.OPEN,
        "A person was asked for",
    )
    assert (first.item_count, first.total_cents) == (2, 337)
    assert (queue[1].item_count, queue[1].total_cents) == (0, 0)


async def test_the_queue_can_be_narrowed_by_status_and_by_session(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    second = await open_car(db_session, "lane-2")
    await ask_for_a_person(db_session, session_id)
    await ask_for_a_person(db_session, second)
    theirs = (
        await db_session.execute(select(Handoff).where(Handoff.session_id == second))
    ).scalar_one()
    await handoffs.acknowledge(db_session, theirs.id, by="Sam", now=NOW)

    async def sessions_in(**filters: Any) -> list[uuid.UUID]:
        queue = await handoffs.list_handoffs(db_session, tax_rate=TAX, **filters)
        return [entry.session_id for entry in queue]

    assert await sessions_in(status=HandoffStatus.OPEN) == [session_id]
    assert await sessions_in(status=HandoffStatus.ACKNOWLEDGED) == [second]
    assert await sessions_in(status=HandoffStatus.RESOLVED) == []
    assert await sessions_in(session_id=second) == [second]
    assert await sessions_in(session_id=uuid.uuid4()) == []


async def test_an_empty_queue_is_an_empty_list(db_session: AsyncSession) -> None:
    assert await handoffs.list_handoffs(db_session, tax_rate=TAX) == []


# --- Picking one up ---------------------------------------------------------


async def test_acknowledging_records_who_and_when(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)

    await handoffs.acknowledge(db_session, handoff.id, by="Sam", now=NOW)

    shown = await package(db_session, handoff)
    assert (shown.status, shown.acknowledged_by, shown.acknowledged_at) == (
        HandoffStatus.ACKNOWLEDGED,
        "Sam",
        NOW,
    )


async def test_acknowledging_twice_keeps_the_first_person(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    await handoffs.acknowledge(db_session, handoff.id, by="Sam", now=NOW)

    await handoffs.acknowledge(
        db_session, handoff.id, by="Alex", now=NOW + timedelta(minutes=5)
    )

    shown = await package(db_session, handoff)
    assert (shown.acknowledged_by, shown.acknowledged_at) == ("Sam", NOW)


async def test_acknowledging_changes_nothing_about_the_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    before = await package(db_session, handoff)

    await handoffs.acknowledge(db_session, handoff.id, by="Sam", now=NOW)

    after = await package(db_session, handoff)
    assert (after.cart, after.stock) == (before.cart, before.stock)
    assert await cookies(db_session) == (5, 2)


# --- Placing the order ------------------------------------------------------


async def test_placing_the_order_charges_what_the_cart_showed(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    cart_before = (await package(db_session, handoff)).cart

    await handoffs.resolve(
        db_session,
        handoff.id,
        HandoffOutcome.ORDER_PLACED,
        now=NOW,
        tax_rate=TAX,
        note="Paid by cheque at the window.",
    )

    shown = await package(db_session, handoff)
    assert (shown.status, shown.outcome, shown.resolved_at) == (
        HandoffStatus.RESOLVED,
        HandoffOutcome.ORDER_PLACED,
        NOW,
    )
    assert shown.note == "Paid by cheque at the window."
    assert shown.order is not None
    assert shown.order.lines == cart_before.lines
    assert shown.order.total_cents == cart_before.total_cents == 337
    order = (await db_session.execute(select(Order))).scalar_one()
    assert order.session_id == session_id


async def test_placing_the_order_takes_the_stock(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    assert await cookies(db_session) == (5, 2)

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.ORDER_PLACED, now=NOW, tax_rate=TAX
    )

    assert await cookies(db_session) == (3, 0)
    (held,) = (await package(db_session, handoff)).stock
    assert (held.status, held.held) == (ReservationStatus.COMMITTED, False)


async def test_the_session_stays_handed_off_after_its_order_is_placed(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """An ended session never changes state. That it was handed to a person
    is part of its history."""
    handoff = await two_cookies_then_a_person(db_session, session_id)
    before = await get_session(db_session, session_id)
    ended_at, end_reason = before.ended_at, before.end_reason

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.ORDER_PLACED, now=NOW, tax_rate=TAX
    )

    after = await get_session(db_session, session_id)
    assert after.state is SessionState.HANDED_OFF
    assert (after.ended_at, after.end_reason) == (ended_at, end_reason)


async def test_an_order_keeps_the_combo_the_customer_had_earned(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "classic_burger", tax_rate=TAX)
    await add_item(
        db_session, session_id, "fries", 1, {"size": ["small"]}, tax_rate=TAX
    )
    await add_item(db_session, session_id, "bottled_water", tax_rate=TAX)
    await ask_for_a_person(db_session, session_id)
    handoff = await the_handoff(db_session)

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.ORDER_PLACED, now=NOW, tax_rate=TAX
    )

    order = (await package(db_session, handoff)).order
    assert order is not None
    assert (order.discount_cents, order.total_cents) == (200, 844)


async def test_an_empty_cart_cannot_be_placed_and_the_handoff_stays_open(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await ask_for_a_person(db_session, session_id)
    handoff = await the_handoff(db_session)

    with pytest.raises(EmptyCart):
        async with db_session.begin_nested():
            await handoffs.resolve(
                db_session,
                handoff.id,
                HandoffOutcome.ORDER_PLACED,
                now=NOW,
                tax_rate=TAX,
            )

    shown = await package(db_session, handoff)
    assert (shown.status, shown.outcome, shown.order) == (
        HandoffStatus.OPEN,
        None,
        None,
    )


async def test_if_the_stock_has_gone_the_order_is_refused_and_nothing_changes(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The hold ran out while the session waited, and another car bought the
    cookies. The person is told, and can cancel instead."""
    handoff = await two_cookies_then_a_person(db_session, session_id, cookies_on_hand=2)
    await db_session.execute(
        update(Reservation).values(expires_at=func.now() - timedelta(minutes=1))
    )
    other = await open_car(db_session, "lane-2")
    await add_item(db_session, other, "cookie", 2, tax_rate=TAX)
    await submit_order(db_session, other, tax_rate=TAX)

    with pytest.raises(InsufficientStock):
        async with db_session.begin_nested():
            await handoffs.resolve(
                db_session,
                handoff.id,
                HandoffOutcome.ORDER_PLACED,
                now=NOW,
                tax_rate=TAX,
            )

    assert (await package(db_session, handoff)).status is HandoffStatus.OPEN
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 1

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.CANCELLED, now=NOW, tax_rate=TAX
    )
    assert (await package(db_session, handoff)).outcome is HandoffOutcome.CANCELLED


async def test_only_a_handed_off_session_can_be_finished_by_a_person(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    with pytest.raises(SessionNotHandedOff) as error:
        await place_handed_off_order(db_session, session_id, tax_rate=TAX)

    assert error.value.details == {"state": "ordering"}


# --- Cancelling -------------------------------------------------------------


async def test_cancelling_frees_the_stock_at_once(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id, cookies_on_hand=2)
    other = await open_car(db_session, "lane-2")
    with pytest.raises(InsufficientStock):
        async with db_session.begin_nested():
            await add_item(db_session, other, "cookie", tax_rate=TAX)

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.CANCELLED, now=NOW, tax_rate=TAX
    )

    assert await cookies(db_session) == (2, 0)
    cart = await add_item(db_session, other, "cookie", 2, tax_rate=TAX)
    assert cart.item_count == 2


async def test_cancelling_places_no_order_and_says_so(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)

    await handoffs.resolve(
        db_session,
        handoff.id,
        HandoffOutcome.CANCELLED,
        now=NOW,
        tax_rate=TAX,
        note="Customer drove off.",
    )

    shown = await package(db_session, handoff)
    assert (shown.status, shown.outcome, shown.note) == (
        HandoffStatus.RESOLVED,
        HandoffOutcome.CANCELLED,
        "Customer drove off.",
    )
    assert shown.order is None
    (held,) = shown.stock
    assert (held.status, held.held) == (ReservationStatus.RELEASED, False)
    # The cart is still shown: it is the record of what they had asked for.
    assert [line.quantity for line in shown.cart.lines] == [2]


# --- A resolved handoff is final --------------------------------------------


@pytest.mark.parametrize("first", list(HandoffOutcome))
async def test_a_resolved_handoff_cannot_be_changed(
    db_session: AsyncSession, session_id: uuid.UUID, first: HandoffOutcome
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)
    await handoffs.resolve(db_session, handoff.id, first, now=NOW, tax_rate=TAX)
    stock_after_first = await cookies(db_session)

    for second in HandoffOutcome:
        with pytest.raises(HandoffResolved) as error:
            await handoffs.resolve(
                db_session, handoff.id, second, now=NOW, tax_rate=TAX
            )
        assert error.value.details == {"outcome": first.value}
    with pytest.raises(HandoffResolved):
        await handoffs.acknowledge(db_session, handoff.id, by="Sam", now=NOW)

    assert await cookies(db_session) == stock_after_first
    assert (await package(db_session, handoff)).outcome is first


async def test_a_handoff_can_be_resolved_without_being_acknowledged_first(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    handoff = await two_cookies_then_a_person(db_session, session_id)

    await handoffs.resolve(
        db_session, handoff.id, HandoffOutcome.ORDER_PLACED, now=NOW, tax_rate=TAX
    )

    shown = await package(db_session, handoff)
    assert (shown.status, shown.acknowledged_by) == (HandoffStatus.RESOLVED, None)


# --- Over HTTP --------------------------------------------------------------


@pytest.fixture
async def handed_off(client: httpx.AsyncClient, menu: None) -> dict[str, Any]:
    """A session with two cookies, handed off through the agent's endpoint.
    Returns its entry in the queue."""
    session_id = (await client.post("/sessions")).json()["session_id"]
    await client.post(
        f"/sessions/{session_id}/cart/lines",
        json={"item_code": "cookie", "quantity": 2},
    )
    await client.post(
        f"/sessions/{session_id}/actions",
        json={"type": "add_item", "item_code": "cookie", "quantity": 400},
    )
    pending = (
        await client.post(
            f"/sessions/{session_id}/actions",
            json={"type": "add_item", "item_code": "cookie", "quantity": 400},
        )
    ).json()
    confirmed = await client.post(
        f"/sessions/{session_id}/pending-actions/{pending['pending_action_id']}/confirm"
    )
    assert confirmed.json()["decision"] == "escalate"
    (entry,) = (await client.get("/handoffs")).json()
    return entry


async def test_the_queue_endpoint_lists_what_is_waiting(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    assert handed_off["status"] == "open"
    assert handed_off["reason"] == {
        "code": "extreme_quantity",
        "title": "Very large order",
    }
    assert (handed_off["item_count"], handed_off["total_cents"]) == (2, 337)
    assert handed_off["acknowledged_by"] is None

    by_status = await client.get("/handoffs", params={"status": "resolved"})
    by_session = await client.get(
        "/handoffs", params={"session_id": handed_off["session_id"]}
    )
    assert by_status.json() == []
    assert by_session.json() == [handed_off]


async def test_the_package_endpoint_returns_everything(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    response = await client.get(f"/handoffs/{handed_off['handoff_id']}")

    assert response.status_code == 200
    shown = response.json()
    assert shown["outstanding"].startswith(
        "The customer asked for 400 of Chocolate chip cookie and confirmed it."
    )
    assert shown["outstanding"].endswith(
        "The cart holds 2 items and can be placed as it stands."
    )
    assert shown["details"] == {"line_quantity": 400, "cart_item_count": 402}
    assert [line["item_code"] for line in shown["cart"]["lines"]] == ["cookie"]
    assert shown["stock"][0]["quantity"] == 2
    assert shown["stock"][0]["held"] is True
    assert (shown["transcript"], shown["steps"], shown["order"]) == ([], [], None)


async def test_acknowledging_over_http(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    url = f"/handoffs/{handed_off['handoff_id']}/acknowledge"

    named = (await client.post(url, json={"by": "Sam"})).json()
    again = (await client.post(url)).json()

    assert (named["status"], named["acknowledged_by"]) == ("acknowledged", "Sam")
    assert again["acknowledged_by"] == "Sam"
    assert again["acknowledged_at"] == named["acknowledged_at"]


async def test_acknowledging_with_no_name_records_staff(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    response = await client.post(f"/handoffs/{handed_off['handoff_id']}/acknowledge")

    assert response.json()["acknowledged_by"] == "staff"


async def test_placing_the_order_over_http(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    response = await client.post(
        f"/handoffs/{handed_off['handoff_id']}/resolve",
        json={"outcome": "order_placed", "note": "Two cookies only."},
    )

    assert response.status_code == 200
    shown = response.json()
    assert (shown["status"], shown["outcome"], shown["note"]) == (
        "resolved",
        "order_placed",
        "Two cookies only.",
    )
    assert shown["order"]["total_cents"] == 337
    read_back = await client.get(f"/orders/{shown['order']['order_id']}")
    assert read_back.json() == shown["order"]
    session = (await client.get(f"/sessions/{handed_off['session_id']}")).json()
    assert session["state"] == "handed_off"


async def test_resolving_twice_over_http_is_a_conflict(
    client: httpx.AsyncClient, handed_off: dict[str, Any]
) -> None:
    url = f"/handoffs/{handed_off['handoff_id']}/resolve"
    await client.post(url, json={"outcome": "cancelled"})

    response = await client.post(url, json={"outcome": "order_placed"})

    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"]) == (
        "handoff_resolved",
        {"outcome": "cancelled"},
    )
    assert (await client.get(f"/handoffs/{handed_off['handoff_id']}")).json()[
        "order"
    ] is None


async def test_placing_an_empty_cart_over_http_is_a_conflict_and_stays_open(
    client: httpx.AsyncClient, db_session: AsyncSession, menu: None
) -> None:
    session_id = await open_car(db_session)
    await ask_for_a_person(db_session, session_id)
    await db_session.commit()
    # Read now: the failed request rolls the session back, which would leave
    # the loaded row needing a reload.
    handoff_id = (await the_handoff(db_session)).id

    response = await client.post(
        f"/handoffs/{handoff_id}/resolve", json={"outcome": "order_placed"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "empty_cart"
    assert (await client.get(f"/handoffs/{handoff_id}")).json()["status"] == "open"


async def test_an_unknown_handoff_is_not_found(client: httpx.AsyncClient) -> None:
    unknown = uuid.uuid4()

    responses = [
        await client.get(f"/handoffs/{unknown}"),
        await client.post(f"/handoffs/{unknown}/acknowledge"),
        await client.post(
            f"/handoffs/{unknown}/resolve", json={"outcome": "cancelled"}
        ),
    ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "handoff_not_found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"outcome": "refunded"},
        {"outcome": "cancelled", "total_cents": 0},
        {"outcome": "cancelled", "note": "x" * 501},
    ],
)
async def test_a_bad_resolution_is_rejected(
    client: httpx.AsyncClient, handed_off: dict[str, Any], body: dict[str, Any]
) -> None:
    """In particular, nothing in the request can set what the order costs."""
    url = f"/handoffs/{handed_off['handoff_id']}"

    response = await client.post(f"{url}/resolve", json=body)

    assert response.status_code == 422
    assert (await client.get(url)).json()["status"] == "open"


async def test_the_session_keeps_no_customer_session_rows_out_of_step(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Every handed-off session has exactly one handoff, and no other session
    has one."""
    other = await open_car(db_session, "lane-2")
    await ask_for_a_person(db_session, session_id)
    await gateway.abandon(db_session, other, tax_rate=TAX)

    handed_off_ids = set(
        (
            await db_session.execute(
                select(CustomerSession.id).where(
                    CustomerSession.state == SessionState.HANDED_OFF
                )
            )
        ).scalars()
    )
    with_handoff = set((await db_session.execute(select(Handoff.session_id))).scalars())

    assert handed_off_ids == with_handoff == {session_id}
