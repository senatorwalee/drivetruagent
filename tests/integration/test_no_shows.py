"""No-shows, flagged and blocked numbers, and what they do to the next order.
Real menu, real database."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    Caller,
    CallerStatusChange,
    CustomerSession,
    Handoff,
    Order,
)
from drivetruagent.enums import CallerStatus, Channel, PickupStatus, SessionState
from drivetruagent.ordering.callers import (
    CallerSummary,
    caller_summaries,
    delete_caller,
    forget_inactive,
    set_status,
)
from drivetruagent.ordering.cart import add_item, request_confirmation
from drivetruagent.ordering.errors import NotAPickupOrder, OrderNotFound
from drivetruagent.ordering.orders import submit_order
from drivetruagent.ordering.pickups import list_pickups, mark_pickup
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import AddItem, RequestConfirmation, SubmitOrder
from drivetruagent.policy.caller_rules import CallerLimits, Standing
from drivetruagent.policy.callers import session_standing
from drivetruagent.policy.decisions import Decision, PolicyLimits

pytestmark = pytest.mark.usefixtures("menu")

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
CALLERS = LIMITS.callers
REGULAR = "+14165550123"
LATE = "+16475550199"


def now() -> datetime:
    return datetime.now(UTC)


async def phone_order(
    db: AsyncSession, phone: str | None, *, cookies: int = 1
) -> Order:
    """A placed phone order, straight through the ordering rules."""
    session_id = (await open_session(db, channel=Channel.PHONE, caller_phone=phone)).id
    await add_item(db, session_id, "cookie", cookies, {}, tax_rate=TAX)
    await request_confirmation(db, session_id, tax_rate=TAX)
    view = await submit_order(db, session_id, tax_rate=TAX)
    await db.commit()
    order = await db.get(Order, view.order_id)
    assert order is not None
    return order


async def no_shows(db: AsyncSession, phone: str, count: int) -> None:
    for _ in range(count):
        order = await phone_order(db, phone)
        await mark_pickup(db, order.id, PickupStatus.NO_SHOW, by="Sam", now=now())
    await db.commit()


async def caller_id(db: AsyncSession, phone: str) -> uuid.UUID:
    return (
        await db.execute(select(Caller.id).where(Caller.phone_e164 == phone))
    ).scalar_one()


async def summary(
    db: AsyncSession, phone: str, *, at: datetime | None = None
) -> CallerSummary:
    (found,) = await caller_summaries(
        db,
        now=at or now(),
        no_show_window=CALLERS.no_show_window,
        repeat_call_window=CALLERS.repeat_call_window,
        phone_e164=phone,
    )
    return found


# --- Pickups ----------------------------------------------------------------


async def test_a_phone_order_waits_to_be_picked_up(db_session: AsyncSession) -> None:
    order = await phone_order(db_session, REGULAR, cookies=2)

    (pickup,) = await list_pickups(db_session, now=now())

    assert order.pickup_status is PickupStatus.WAITING
    assert (pickup.order_number, pickup.caller_last_four) == (
        order.order_number,
        "0123",
    )
    assert [(line.quantity, line.item_name) for line in pickup.lines] == [
        (2, "Chocolate chip cookie")
    ]
    assert (pickup.marked_by, pickup.marked_at) == (None, None)


async def test_a_drive_through_order_has_no_pickup(db_session: AsyncSession) -> None:
    session_id = (await open_session(db_session)).id
    await add_item(db_session, session_id, "cookie", 1, {}, tax_rate=TAX)
    await request_confirmation(db_session, session_id, tax_rate=TAX)
    view = await submit_order(db_session, session_id, tax_rate=TAX)

    assert await list_pickups(db_session, now=now()) == []
    with pytest.raises(NotAPickupOrder):
        await mark_pickup(
            db_session, view.order_id, PickupStatus.NO_SHOW, by="Sam", now=now()
        )


async def test_marking_records_who_and_when(db_session: AsyncSession) -> None:
    order = await phone_order(db_session, REGULAR)
    at = now()

    pickup = await mark_pickup(
        db_session, order.id, PickupStatus.PICKED_UP, by="Sam", now=at
    )

    assert (pickup.status, pickup.marked_by, pickup.marked_at) == (
        PickupStatus.PICKED_UP,
        "Sam",
        at,
    )


async def test_a_wrong_mark_can_be_put_right(db_session: AsyncSession) -> None:
    order = await phone_order(db_session, REGULAR)
    await mark_pickup(db_session, order.id, PickupStatus.NO_SHOW, by="Sam", now=now())

    fixed = await mark_pickup(
        db_session, order.id, PickupStatus.PICKED_UP, by="Alex", now=now()
    )

    assert (fixed.status, fixed.marked_by) == (PickupStatus.PICKED_UP, "Alex")
    assert (await summary(db_session, REGULAR)).recent_no_shows == 0


async def test_marking_the_same_way_again_keeps_the_first_mark(
    db_session: AsyncSession,
) -> None:
    order = await phone_order(db_session, REGULAR)
    first = await mark_pickup(
        db_session, order.id, PickupStatus.NO_SHOW, by="Sam", now=now()
    )

    again = await mark_pickup(
        db_session, order.id, PickupStatus.NO_SHOW, by="Alex", now=now()
    )

    assert (again.marked_by, again.marked_at) == ("Sam", first.marked_at)


async def test_the_list_shows_waiting_first_then_recent_marks(
    db_session: AsyncSession,
) -> None:
    collected = await phone_order(db_session, REGULAR)
    waiting = await phone_order(db_session, LATE)
    long_ago = await phone_order(db_session, LATE)
    await mark_pickup(
        db_session, collected.id, PickupStatus.PICKED_UP, by="Sam", now=now()
    )
    await mark_pickup(
        db_session,
        long_ago.id,
        PickupStatus.PICKED_UP,
        by="Sam",
        now=now() - timedelta(days=1),
    )

    listed = await list_pickups(db_session, now=now())

    assert [p.order_id for p in listed] == [waiting.id, collected.id]


async def test_an_unknown_order_cannot_be_marked(db_session: AsyncSession) -> None:
    with pytest.raises(OrderNotFound):
        await mark_pickup(
            db_session, uuid.uuid4(), PickupStatus.NO_SHOW, by="Sam", now=now()
        )


# --- Counting ---------------------------------------------------------------


async def test_no_shows_are_counted_against_the_number(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, LATE, 2)
    await phone_order(db_session, LATE)  # still waiting: not a no-show

    found = await summary(db_session, LATE)

    assert (found.order_count, found.recent_no_shows) == (3, 2)


async def test_no_shows_stop_counting_once_they_are_old(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, LATE, 2)

    later = now() + CALLERS.no_show_window + timedelta(days=1)

    assert (await summary(db_session, LATE, at=later)).recent_no_shows == 0


async def test_calls_without_an_order_are_counted(db_session: AsyncSession) -> None:
    await phone_order(db_session, REGULAR)
    for _ in range(3):
        await open_session(db_session, channel=Channel.PHONE, caller_phone=REGULAR)

    found = await summary(db_session, REGULAR)

    assert (found.call_count, found.recent_calls_without_order) == (4, 3)
    later = now() + CALLERS.repeat_call_window + timedelta(minutes=1)
    assert (
        await summary(db_session, REGULAR, at=later)
    ).recent_calls_without_order == 0


# --- Staff decisions --------------------------------------------------------


async def test_a_status_change_is_recorded_with_who_and_why(
    db_session: AsyncSession,
) -> None:
    await phone_order(db_session, LATE)
    caller = await caller_id(db_session, LATE)

    changed = await set_status(
        db_session,
        caller,
        CallerStatus.BLOCKED,
        reason="Prank calls",
        by="Manager",
        now=now(),
    )

    assert changed
    found = await summary(db_session, LATE)
    assert (found.status, found.status_reason, found.status_changed_by) == (
        CallerStatus.BLOCKED,
        "Prank calls",
        "Manager",
    )
    (change,) = (await db_session.execute(select(CallerStatusChange))).scalars()
    assert (change.from_status, change.to_status) == (
        CallerStatus.NORMAL,
        CallerStatus.BLOCKED,
    )


async def test_setting_the_same_status_again_records_nothing(
    db_session: AsyncSession,
) -> None:
    await phone_order(db_session, LATE)
    caller = await caller_id(db_session, LATE)
    await set_status(
        db_session, caller, CallerStatus.FLAGGED, reason="first", by="A", now=now()
    )

    changed = await set_status(
        db_session, caller, CallerStatus.FLAGGED, reason="again", by="B", now=now()
    )

    assert not changed
    assert (await summary(db_session, LATE)).status_reason == "first"


async def test_deleting_a_caller_forgets_its_no_shows_and_history(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, LATE, 2)
    caller = await caller_id(db_session, LATE)
    await set_status(
        db_session, caller, CallerStatus.BLOCKED, reason="x", by="A", now=now()
    )

    last_four = await delete_caller(db_session, caller)

    assert last_four == "0199"
    assert (await db_session.execute(select(Caller))).first() is None
    assert (await db_session.execute(select(CallerStatusChange))).first() is None
    # The orders stay, so the day's takings still add up.
    assert len((await db_session.execute(select(Order))).all()) == 2


async def test_numbers_not_heard_from_in_a_year_are_forgotten(
    db_session: AsyncSession,
) -> None:
    await phone_order(db_session, REGULAR)
    await phone_order(db_session, LATE)
    await db_session.execute(
        update(Caller)
        .where(Caller.phone_e164 == LATE)
        .values(last_call_at=now() - timedelta(days=400))
    )

    forgotten = await forget_inactive(
        db_session, called_before=now() - timedelta(days=365)
    )

    assert forgotten == 1
    phones = (await db_session.execute(select(Caller.phone_e164))).scalars().all()
    assert phones == [REGULAR]


# --- Standing ---------------------------------------------------------------


async def standing_of(db: AsyncSession, phone: str | None) -> Standing | None:
    session = await open_session(db, channel=Channel.PHONE, caller_phone=phone)
    return await session_standing(db, session, CALLERS, now=now())


async def test_two_recent_no_shows_flag_a_number(db_session: AsyncSession) -> None:
    await no_shows(db_session, LATE, 1)
    assert await standing_of(db_session, LATE) is Standing.NORMAL

    await no_shows(db_session, LATE, 1)
    assert await standing_of(db_session, LATE) is Standing.FLAGGED


async def test_staff_can_flag_a_number_themselves(db_session: AsyncSession) -> None:
    await phone_order(db_session, LATE)
    await set_status(
        db_session,
        await caller_id(db_session, LATE),
        CallerStatus.FLAGGED,
        reason="Called in a fake order",
        by="Manager",
        now=now(),
    )

    assert await standing_of(db_session, LATE) is Standing.FLAGGED


async def test_a_withheld_number_and_a_car_have_their_own_standing(
    db_session: AsyncSession,
) -> None:
    car = await open_session(db_session)

    assert await standing_of(db_session, None) is Standing.UNKNOWN
    assert await session_standing(db_session, car, CALLERS, now=now()) is None


# --- Big orders -------------------------------------------------------------


async def big_order(db: AsyncSession, phone: str | None) -> uuid.UUID:
    """A phone order of $40.61 with tax: just over the $40 cap."""
    session_id = (await open_session(db, channel=Channel.PHONE, caller_phone=phone)).id
    await gateway.execute(
        db,
        session_id,
        AddItem(item_code="cheeseburger", quantity=6),
        tax_rate=TAX,
        limits=LIMITS,
    )
    return session_id


async def test_a_big_order_from_a_flagged_number_goes_to_a_person(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, LATE, 2)
    session_id = await big_order(db_session, LATE)

    outcome = await gateway.execute(
        db_session, session_id, RequestConfirmation(), tax_rate=TAX, limits=LIMITS
    )

    assert (outcome.decision, outcome.rule) == (Decision.ESCALATE, "large_order_check")
    session = await db_session.get(CustomerSession, session_id)
    assert session is not None and session.state is SessionState.HANDED_OFF
    handoff = (
        await db_session.execute(
            select(Handoff).where(Handoff.session_id == session_id)
        )
    ).scalar_one()
    assert handoff.reason == "large_order_check"
    assert handoff.details["standing"] == "flagged"
    assert handoff.details["total_cents"] > CALLERS.large_order_cents


async def test_a_big_order_from_a_withheld_number_goes_to_a_person(
    db_session: AsyncSession,
) -> None:
    session_id = await big_order(db_session, None)

    outcome = await gateway.execute(
        db_session, session_id, RequestConfirmation(), tax_rate=TAX, limits=LIMITS
    )

    assert outcome.rule == "large_order_check"


async def test_a_big_order_from_a_good_number_is_placed(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, REGULAR, 1)
    session_id = await big_order(db_session, REGULAR)

    for action in (RequestConfirmation(), SubmitOrder()):
        outcome = await gateway.execute(
            db_session, session_id, action, tax_rate=TAX, limits=LIMITS
        )
        assert outcome.decision is Decision.ALLOW
    assert outcome.order is not None


async def test_a_small_order_from_a_flagged_number_is_placed(
    db_session: AsyncSession,
) -> None:
    await no_shows(db_session, LATE, 2)
    session_id = (
        await open_session(db_session, channel=Channel.PHONE, caller_phone=LATE)
    ).id

    for action in (AddItem(item_code="cookie"), RequestConfirmation(), SubmitOrder()):
        outcome = await gateway.execute(
            db_session, session_id, action, tax_rate=TAX, limits=LIMITS
        )
        assert outcome.decision is Decision.ALLOW


async def test_the_cap_is_a_setting(db_session: AsyncSession) -> None:
    session_id = await big_order(db_session, None)
    generous = PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
        callers=CallerLimits(large_order_cents=10_000),
    )

    outcome = await gateway.execute(
        db_session, session_id, RequestConfirmation(), tax_rate=TAX, limits=generous
    )

    assert outcome.decision is Decision.ALLOW


# --- The API ----------------------------------------------------------------


async def test_staff_mark_a_pickup_through_the_api(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    order = await phone_order(db_session, LATE)

    listed = (await client.get("/pickups")).json()
    marked = await client.post(
        f"/pickups/{order.id}", json={"status": "no_show", "by": "Sam"}
    )

    assert [p["order_number"] for p in listed] == [order.order_number]
    assert marked.status_code == 200
    assert (marked.json()["status"], marked.json()["marked_by"]) == ("no_show", "Sam")


@pytest.mark.parametrize("status", ["waiting", "lost"])
async def test_a_pickup_is_marked_one_of_two_ways(
    client: httpx.AsyncClient, db_session: AsyncSession, status: str
) -> None:
    order = await phone_order(db_session, LATE)

    response = await client.post(f"/pickups/{order.id}", json={"status": status})

    assert response.status_code == 422


async def test_marking_a_drive_through_order_is_a_conflict(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    session_id = (await open_session(db_session)).id
    await add_item(db_session, session_id, "cookie", 1, {}, tax_rate=TAX)
    await request_confirmation(db_session, session_id, tax_rate=TAX)
    view = await submit_order(db_session, session_id, tax_rate=TAX)
    await db_session.commit()

    response = await client.post(
        f"/pickups/{view.order_id}", json={"status": "no_show"}
    )
    missing = await client.post(f"/pickups/{uuid.uuid4()}", json={"status": "no_show"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_a_pickup_order"
    assert missing.status_code == 404


async def test_the_callers_page_shows_standing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await no_shows(db_session, LATE, 2)
    await phone_order(db_session, REGULAR)

    listed = {c["phone_e164"]: c for c in (await client.get("/callers")).json()}

    assert listed[LATE]["standing"] == "flagged"
    assert listed[LATE]["recent_no_shows"] == 2
    assert listed[REGULAR]["standing"] == "normal"


async def test_staff_block_and_unblock_a_number(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await phone_order(db_session, LATE)
    caller = await caller_id(db_session, LATE)

    blocked = await client.post(
        f"/callers/{caller}/status",
        json={"status": "blocked", "reason": "Abusive calls", "by": "Manager"},
    )
    cleared = await client.post(
        f"/callers/{caller}/status",
        json={"status": "normal", "reason": "Apologised in person"},
    )

    assert blocked.json()["standing"] == "blocked"
    assert blocked.json()["status_reason"] == "Abusive calls"
    assert cleared.json()["standing"] == "normal"
    assert cleared.json()["status_changed_by"] == "staff"


@pytest.mark.parametrize(
    "body", [{"status": "blocked"}, {"status": "blocked", "reason": ""}]
)
async def test_a_status_change_needs_a_reason(
    client: httpx.AsyncClient, db_session: AsyncSession, body: dict[str, str]
) -> None:
    await phone_order(db_session, LATE)
    caller = await caller_id(db_session, LATE)

    response = await client.post(f"/callers/{caller}/status", json=body)

    assert response.status_code == 422


async def test_staff_delete_a_number(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await phone_order(db_session, LATE)
    caller = await caller_id(db_session, LATE)

    deleted = await client.delete(f"/callers/{caller}")
    again = await client.delete(f"/callers/{caller}")

    assert deleted.status_code == 204
    assert again.status_code == 404
    assert (await client.get("/callers")).json() == []
