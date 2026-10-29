"""Session timers against a real database.

The rules are covered in tests/unit/test_timers.py. These tests check that
what is due actually happens, once, and that nothing else does.

No test waits for a timer. Every call is told what time it is, as seconds
after the session started: 20 seconds of silence before the customer is
asked if they are still there, 20 more before the session is closed, and
three minutes before a person takes over.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events, timekeeper
from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.messages import AssistantMessage, Message
from drivetruagent.agent.model import ModelTurn, ModelUnavailable, ToolDefinition
from drivetruagent.agent.scripted import ScriptedModel, call, say
from drivetruagent.agent.timekeeper import (
    HANDING_OFF,
    PHONE_HANDING_OFF,
    STILL_THERE,
    SweepReport,
)
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import CustomerSession, PendingAction, Reservation
from drivetruagent.enums import (
    AgentEventKind,
    Channel,
    Language,
    PendingActionStatus,
    ReservationStatus,
    SessionState,
)
from drivetruagent.ordering import sessions
from drivetruagent.ordering.cart import add_item, get_cart
from drivetruagent.ordering.errors import (
    InsufficientStock,
    SessionEnded,
    SessionNotFound,
)
from drivetruagent.ordering.inventory import set_on_hand, stock_levels
from drivetruagent.ordering.sessions import get_session, open_session, set_language
from drivetruagent.ordering.timers import TimerAction, TimerSettings
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import AddItem
from drivetruagent.policy.decisions import Decision, PolicyLimits

TAX = Decimal("0.13")
# The tests count in a three-minute limit, whatever the default.
TIMERS = TimerSettings(max_duration=timedelta(minutes=3))
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)
AGENT = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=TAX,
    limits=LIMITS,
    max_tool_rounds=4,
    timers=TIMERS,
)

pytestmark = pytest.mark.usefixtures("menu")


class Car:
    """One session, and a way to say what time it is for it."""

    def __init__(self, customer_session: CustomerSession) -> None:
        self.id = customer_session.id
        self.started_at = customer_session.started_at

    def at(self, seconds: float) -> datetime:
        """A time, as seconds after this session started."""
        return self.started_at + timedelta(seconds=seconds)


async def open_car(db: AsyncSession, lane: str = "lane-1") -> Car:
    customer_session = await open_session(db, lane)
    await db.commit()
    return Car(customer_session)


@pytest.fixture
async def car(db_session: AsyncSession, menu: None) -> Car:
    return await open_car(db_session)


async def enforce(db: AsyncSession, car: Car, seconds: float) -> TimerAction:
    return await timekeeper.enforce(
        db, car.id, now=car.at(seconds), timers=TIMERS, tax_rate=TAX
    )


async def sweep(db: AsyncSession, car: Car, seconds: float) -> SweepReport:
    return await timekeeper.sweep(db, now=car.at(seconds), timers=TIMERS, tax_rate=TAX)


async def talk(db: AsyncSession, car: Car, started: float, ended: float) -> None:
    """A whole turn: the customer speaks at `started`, the agent is done at
    `ended`."""
    await sessions.begin_turn(db, car.id, now=car.at(started))
    await sessions.end_turn(db, car.id, now=car.at(ended))


async def log(db: AsyncSession, car: Car) -> list[tuple[str, Any]]:
    """Every event of a session as (kind, text or payload)."""
    return [
        (event.kind.value, event.payload.get("text", event.payload))
        for event in await events.load_events(db, car.id)
    ]


async def said(db: AsyncSession, car: Car) -> list[str]:
    """Everything the agent has said, in order."""
    return [
        event.payload["text"]
        for event in await events.load_events(db, car.id)
        if event.kind is AgentEventKind.ASSISTANT_MESSAGE
    ]


# --- The nudge --------------------------------------------------------------


async def test_nothing_happens_before_twenty_seconds(
    db_session: AsyncSession, car: Car
) -> None:
    assert await enforce(db_session, car, 19) is TimerAction.NONE
    assert await log(db_session, car) == []


async def test_twenty_seconds_of_silence_asks_if_the_customer_is_there(
    db_session: AsyncSession, car: Car
) -> None:
    action = await enforce(db_session, car, 20)

    assert action is TimerAction.NUDGE
    assert await log(db_session, car) == [
        ("timer", {"action": "nudge", "rule": "silence", "after_seconds": 20}),
        ("assistant_message", "Are you still there?"),
    ]
    customer_session = await get_session(db_session, car.id)
    assert customer_session.nudged_at == car.at(20)
    assert customer_session.state is SessionState.GREETING


async def test_the_customer_is_asked_in_their_language(
    db_session: AsyncSession, car: Car
) -> None:
    await set_language(db_session, car.id, Language.FR)

    await enforce(db_session, car, 20)

    assert await said(db_session, car) == ["Êtes-vous toujours là?"]


async def test_the_customer_is_asked_once_not_every_second(
    db_session: AsyncSession, car: Car
) -> None:
    for second in range(20, 40):
        await enforce(db_session, car, second)

    assert await said(db_session, car) == ["Are you still there?"]


async def test_the_model_can_see_that_the_customer_was_asked(
    db_session: AsyncSession, car: Car
) -> None:
    """The nudge is part of the conversation. The timer event behind it is
    bookkeeping, and is not."""
    await enforce(db_session, car, 20)

    assert await events.load_messages(db_session, car.id) == [
        AssistantMessage("Are you still there?")
    ]


async def test_answering_starts_the_silence_again(
    db_session: AsyncSession, car: Car
) -> None:
    await enforce(db_session, car, 20)

    await talk(db_session, car, started=25, ended=27)

    assert (await get_session(db_session, car.id)).nudged_at is None
    assert await enforce(db_session, car, 46) is TimerAction.NONE
    assert await enforce(db_session, car, 47) is TimerAction.NUDGE
    assert await said(db_session, car) == ["Are you still there?"] * 2


# --- Abandoned --------------------------------------------------------------


async def test_no_answer_closes_the_session(db_session: AsyncSession, car: Car) -> None:
    await enforce(db_session, car, 20)

    assert await enforce(db_session, car, 39) is TimerAction.NONE
    assert await enforce(db_session, car, 40) is TimerAction.ABANDON

    customer_session = await get_session(db_session, car.id)
    assert customer_session.state is SessionState.ABANDONED
    assert customer_session.end_reason == "silence_timeout"
    assert (await log(db_session, car))[-1] == (
        "timer",
        {"action": "abandon", "rule": "silence_timeout", "after_seconds": 40},
    )
    # Nothing is said to a customer who is not there.
    assert await said(db_session, car) == ["Are you still there?"]


async def test_an_abandoned_session_gives_its_stock_back(
    db_session: AsyncSession, car: Car
) -> None:
    other = await open_car(db_session, "lane-2")
    await set_on_hand(db_session, "cookie", 1)
    await add_item(db_session, car.id, "cookie", tax_rate=TAX)
    with pytest.raises(InsufficientStock):
        await add_item(db_session, other.id, "cookie", tax_rate=TAX)
    await sessions.end_turn(db_session, car.id, now=car.at(5))

    await enforce(db_session, car, 25)
    await enforce(db_session, car, 45)

    cart = await add_item(db_session, other.id, "cookie", tax_rate=TAX)
    assert cart.item_count == 1


async def test_an_abandoned_session_leaves_no_question_open(
    db_session: AsyncSession, car: Car
) -> None:
    held = await gateway.execute(
        db_session,
        car.id,
        AddItem(item_code="cookie", quantity=15),
        tax_rate=TAX,
        limits=LIMITS,
    )
    assert held.decision is Decision.CONFIRM

    await enforce(db_session, car, 20)
    await enforce(db_session, car, 40)

    pending = (await db_session.execute(select(PendingAction))).scalar_one()
    assert pending.status is PendingActionStatus.SUPERSEDED


# --- Handed off -------------------------------------------------------------


async def test_three_minutes_hands_the_session_to_a_person(
    db_session: AsyncSession, car: Car
) -> None:
    await talk(db_session, car, started=170, ended=175)

    assert await enforce(db_session, car, 179) is TimerAction.NONE
    assert await enforce(db_session, car, 180) is TimerAction.HAND_OFF

    customer_session = await get_session(db_session, car.id)
    assert customer_session.state is SessionState.HANDED_OFF
    assert customer_session.end_reason == "time_limit"
    assert await log(db_session, car) == [
        ("timer", {"action": "hand_off", "rule": "time_limit", "after_seconds": 180}),
        (
            "assistant_message",
            "A team member will help you finish your order. One moment, please.",
        ),
    ]


async def test_the_handoff_is_said_in_the_customers_language(
    db_session: AsyncSession, car: Car
) -> None:
    await set_language(db_session, car.id, Language.FR)
    await talk(db_session, car, started=170, ended=175)

    await enforce(db_session, car, 180)

    assert await said(db_session, car) == [HANDING_OFF[Language.FR]]


async def test_on_the_phone_the_handoff_promises_no_one_will_come_in_a_moment(
    db_session: AsyncSession, menu: None
) -> None:
    call = Car(await open_session(db_session, channel=Channel.PHONE))
    await db_session.commit()
    await talk(db_session, call, started=170, ended=175)

    await enforce(db_session, call, 180)

    assert await said(db_session, call) == [PHONE_HANDING_OFF[Language.EN]]


async def test_a_handed_off_session_keeps_its_cart_and_its_stock(
    db_session: AsyncSession, car: Car
) -> None:
    """A person is about to finish this order. It must still be there."""
    await set_on_hand(db_session, "cookie", 5)
    await add_item(db_session, car.id, "cookie", 2, tax_rate=TAX)
    await talk(db_session, car, started=170, ended=175)

    await enforce(db_session, car, 180)

    cart = await get_cart(db_session, car.id, tax_rate=TAX)
    assert [(line.item_code, line.quantity) for line in cart.lines] == [("cookie", 2)]
    cookies = next(
        level for level in await stock_levels(db_session) if level.item_code == "cookie"
    )
    assert (cookies.reserved, cookies.available) == (2, 3)


async def test_the_clock_does_nothing_to_a_session_that_has_ended(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.end_session(db_session, car.id, SessionState.TERMINATED, "test")

    assert await enforce(db_session, car, 500) is TimerAction.NONE
    assert await log(db_session, car) == []
    assert (await get_session(db_session, car.id)).state is SessionState.TERMINATED


async def test_enforcing_an_unknown_session_is_an_error(
    db_session: AsyncSession, car: Car
) -> None:
    with pytest.raises(SessionNotFound):
        await timekeeper.enforce(
            db_session, uuid.uuid4(), now=car.at(20), timers=TIMERS, tax_rate=TAX
        )


# --- Paused -----------------------------------------------------------------


async def test_a_paused_session_never_times_out(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.pause(db_session, car.id, now=car.at(5))

    for second in (25, 45, 180, 3600):
        assert await enforce(db_session, car, second) is TimerAction.NONE
    assert (await get_session(db_session, car.id)).state is SessionState.GREETING
    assert await log(db_session, car) == []


async def test_resuming_gives_a_fresh_twenty_seconds(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.pause(db_session, car.id, now=car.at(15))

    await sessions.resume(db_session, car.id, now=car.at(500))

    assert await enforce(db_session, car, 519) is TimerAction.NONE
    assert await enforce(db_session, car, 520) is TimerAction.NUDGE


async def test_time_spent_paused_does_not_count_toward_three_minutes(
    db_session: AsyncSession, car: Car
) -> None:
    """Paused from 0:15 to 8:20. Three minutes of running time is at 11:05."""
    await sessions.pause(db_session, car.id, now=car.at(15))
    await sessions.resume(db_session, car.id, now=car.at(500))
    assert (await get_session(db_session, car.id)).paused_seconds == 485
    await talk(db_session, car, started=660, ended=663)

    assert await enforce(db_session, car, 664) is TimerAction.NONE
    assert await enforce(db_session, car, 665) is TimerAction.HAND_OFF


async def test_pausing_after_a_nudge_stops_the_session_closing(
    db_session: AsyncSession, car: Car
) -> None:
    await enforce(db_session, car, 20)
    await sessions.pause(db_session, car.id, now=car.at(30))

    assert await enforce(db_session, car, 60) is TimerAction.NONE

    await sessions.resume(db_session, car.id, now=car.at(60))
    assert (await get_session(db_session, car.id)).nudged_at is None
    assert await enforce(db_session, car, 79) is TimerAction.NONE


async def test_pausing_twice_is_the_same_as_pausing_once(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.pause(db_session, car.id, now=car.at(10))
    await sessions.pause(db_session, car.id, now=car.at(50))

    await sessions.resume(db_session, car.id, now=car.at(60))

    assert (await get_session(db_session, car.id)).paused_seconds == 50


async def test_resuming_a_session_that_is_not_paused_changes_nothing(
    db_session: AsyncSession, car: Car
) -> None:
    before = (await get_session(db_session, car.id)).last_activity_at

    await sessions.resume(db_session, car.id, now=car.at(15))

    customer_session = await get_session(db_session, car.id)
    assert (customer_session.paused_seconds, customer_session.last_activity_at) == (
        0,
        before,
    )
    assert await enforce(db_session, car, 20) is TimerAction.NUDGE


async def test_a_session_that_has_ended_cannot_be_paused(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.end_session(db_session, car.id, SessionState.ABANDONED, "test")

    with pytest.raises(SessionEnded):
        await sessions.pause(db_session, car.id, now=car.at(5))
    with pytest.raises(SessionEnded):
        await sessions.resume(db_session, car.id, now=car.at(5))


async def test_paused_time_cannot_be_negative(
    db_session: AsyncSession, car: Car
) -> None:
    with pytest.raises(IntegrityError, match="ck_sessions_paused_seconds_non_negative"):
        await db_session.execute(update(CustomerSession).values(paused_seconds=-1))


# --- While the agent is replying --------------------------------------------


async def test_no_timer_fires_while_the_agent_is_replying(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.begin_turn(db_session, car.id, now=car.at(10))

    assert await enforce(db_session, car, 45) is TimerAction.NONE

    await sessions.end_turn(db_session, car.id, now=car.at(46))
    assert await enforce(db_session, car, 65) is TimerAction.NONE
    assert await enforce(db_session, car, 66) is TimerAction.NUDGE


async def test_a_turn_that_started_before_three_minutes_is_allowed_to_finish(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.begin_turn(db_session, car.id, now=car.at(178))

    assert await enforce(db_session, car, 182) is TimerAction.NONE

    await sessions.end_turn(db_session, car.id, now=car.at(183))
    assert await enforce(db_session, car, 183) is TimerAction.HAND_OFF


class WatchingModel:
    """Answers at once, noting what the session's turn marker was meanwhile."""

    def __init__(self, db: AsyncSession, session_id: uuid.UUID) -> None:
        self.db = db
        self.session_id = session_id
        self.marker_during_turn: datetime | None = None

    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        customer_session = await get_session(self.db, self.session_id)
        self.marker_during_turn = customer_session.turn_started_at
        return say("Welcome!")


class BrokenModel:
    async def complete(
        self,
        *,
        instructions: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        raise ModelUnavailable("The model did not answer.")


async def test_a_turn_holds_the_clocks_and_lets_go_when_it_ends(
    db_session: AsyncSession, car: Car
) -> None:
    model = WatchingModel(db_session, car.id)
    await enforce(db_session, car, 20)
    await db_session.commit()

    await run_turn(db_session, car.id, "Hi", model=model, config=AGENT)

    customer_session = await get_session(db_session, car.id)
    assert model.marker_during_turn is not None
    assert customer_session.turn_started_at is None
    assert customer_session.nudged_at is None
    assert customer_session.last_activity_at >= model.marker_during_turn


async def test_a_turn_that_fails_still_lets_go_of_the_clocks(
    db_session: AsyncSession, car: Car
) -> None:
    """Otherwise one model outage would leave the session unable to time out."""
    with pytest.raises(ModelUnavailable):
        await run_turn(db_session, car.id, "Hi", model=BrokenModel(), config=AGENT)

    assert (await get_session(db_session, car.id)).turn_started_at is None


async def test_a_customer_speaking_resumes_a_paused_session(
    db_session: AsyncSession, car: Car
) -> None:
    await sessions.pause(db_session, car.id, now=car.at(1))
    await db_session.commit()

    await run_turn(
        db_session, car.id, "Hi", model=ScriptedModel([say("Welcome!")]), config=AGENT
    )

    assert (await get_session(db_session, car.id)).paused_at is None


# --- The length limit without a sweep ---------------------------------------


async def start_long_ago(db: AsyncSession, car: Car, seconds: int) -> None:
    """Make the session one that started `seconds` before the real time now."""
    await db.execute(
        update(CustomerSession)
        .where(CustomerSession.id == car.id)
        .values(
            started_at=CustomerSession.started_at - timedelta(seconds=seconds),
        )
    )
    await db.commit()


async def test_a_customer_who_speaks_after_three_minutes_is_handed_off(
    db_session: AsyncSession, car: Car
) -> None:
    """No sweep has run. The limit holds anyway, and the model is not asked:
    a scripted model with nothing to say would fail if it were called."""
    await add_item(db_session, car.id, "cookie", tax_rate=TAX)
    await start_long_ago(db_session, car, 200)

    reply = await run_turn(
        db_session, car.id, "And a cola", model=ScriptedModel([]), config=AGENT
    )

    assert reply.text == HANDING_OFF[Language.EN]
    assert reply.tools_used == ()
    assert reply.cart.state is SessionState.HANDED_OFF
    assert [line.item_code for line in reply.cart.lines] == ["cookie"]
    assert [kind for kind, _ in await log(db_session, car)] == [
        "user_message",
        "timer",
        "assistant_message",
    ]


async def test_time_paused_is_left_out_of_that_check_too(
    db_session: AsyncSession, car: Car
) -> None:
    await start_long_ago(db_session, car, 200)
    await db_session.execute(update(CustomerSession).values(paused_seconds=100))
    await db_session.commit()

    reply = await run_turn(
        db_session, car.id, "Hi", model=ScriptedModel([say("Welcome!")]), config=AGENT
    )

    assert reply.text == "Welcome!"
    assert reply.cart.state is SessionState.GREETING


async def test_with_no_timers_configured_a_long_session_carries_on(
    db_session: AsyncSession, car: Car
) -> None:
    await start_long_ago(db_session, car, 2000)
    untimed = AgentConfig(
        restaurant_name="Maple Lane Burgers",
        tax_rate=TAX,
        limits=LIMITS,
        max_tool_rounds=4,
    )

    reply = await run_turn(
        db_session, car.id, "Hi", model=ScriptedModel([say("Welcome!")]), config=untimed
    )

    assert reply.text == "Welcome!"


async def test_an_order_can_still_be_placed_just_inside_the_limit(
    db_session: AsyncSession, car: Car
) -> None:
    await add_item(db_session, car.id, "cookie", tax_rate=TAX)
    await start_long_ago(db_session, car, 170)
    model = ScriptedModel(
        [
            call("request_confirmation"),
            say("One cookie. Is that right?"),
            call("submit_order"),
            say("Thanks!"),
        ]
    )

    await run_turn(db_session, car.id, "That's all", model=model, config=AGENT)
    reply = await run_turn(db_session, car.id, "Yes", model=model, config=AGENT)

    assert reply.cart.state is SessionState.SUBMITTED


# --- The sweep --------------------------------------------------------------


async def test_one_sweep_deals_with_every_session_in_progress(
    db_session: AsyncSession, car: Car
) -> None:
    """Five cars, each in a different situation at the same moment."""
    silent = car
    gone, long, fresh, paused = [
        await open_car(db_session, f"lane-{n}") for n in range(2, 6)
    ]
    await enforce(db_session, gone, 20)
    await talk(db_session, long, started=170, ended=178)
    await talk(db_session, fresh, started=175, ended=178)
    await sessions.pause(db_session, paused.id, now=paused.at(5))
    # The silent car last spoke 20 seconds before the sweep; the long one has
    # been going for three minutes; the fresh one only just started.
    await db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.id.in_([silent.id, fresh.id]))
        .values(started_at=CustomerSession.started_at + timedelta(seconds=160))
    )
    await talk(db_session, silent, started=0, ended=0)

    report = await sweep(db_session, car, 180)

    assert report == SweepReport(nudged=1, abandoned=1, handed_off=1)
    states = {
        name: (await get_session(db_session, each.id)).state
        for name, each in [
            ("silent", silent),
            ("gone", gone),
            ("long", long),
            ("fresh", fresh),
            ("paused", paused),
        ]
    }
    assert states == {
        "silent": SessionState.GREETING,
        "gone": SessionState.ABANDONED,
        "long": SessionState.HANDED_OFF,
        "fresh": SessionState.GREETING,
        "paused": SessionState.GREETING,
    }
    assert await said(db_session, silent) == ["Are you still there?"]


async def test_a_sweep_with_nothing_to_do_reports_nothing(
    db_session: AsyncSession, car: Car
) -> None:
    assert await sweep(db_session, car, 5) == SweepReport()


async def test_a_sweep_tidies_up_stock_holds_that_have_expired(
    db_session: AsyncSession, car: Car
) -> None:
    await set_on_hand(db_session, "cookie", 5)
    await add_item(db_session, car.id, "cookie", tax_rate=TAX)
    await db_session.execute(
        update(Reservation).values(expires_at=car.started_at - timedelta(hours=1))
    )

    report = await sweep(db_session, car, 5)

    assert report.reservations_released == 1
    reservation = (await db_session.execute(select(Reservation))).scalar_one()
    assert reservation.status is ReservationStatus.RELEASED


async def test_one_session_that_fails_does_not_stop_the_rest(
    db_session: AsyncSession, car: Car, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = await open_car(db_session, "lane-2")
    real_enforce = timekeeper.enforce

    async def enforce_except_the_first(
        db: AsyncSession, session_id: uuid.UUID, **kwargs: Any
    ) -> TimerAction:
        if session_id == car.id:
            raise RuntimeError("this session is broken")
        return await real_enforce(db, session_id, **kwargs)

    monkeypatch.setattr(timekeeper, "enforce", enforce_except_the_first)

    report = await sweep(db_session, car, 20)

    assert report == SweepReport(nudged=1, failed=1)
    assert await said(db_session, other) == ["Are you still there?"]
    assert await said(db_session, car) == []


# --- Over HTTP --------------------------------------------------------------


@pytest.fixture
async def http_session(client: httpx.AsyncClient, menu: None) -> dict[str, Any]:
    return (await client.post("/sessions")).json()


def seconds_between(earlier: str, later: str) -> float:
    return (
        datetime.fromisoformat(later) - datetime.fromisoformat(earlier)
    ).total_seconds()


async def test_a_new_session_says_when_its_timers_will_fire(
    http_session: dict[str, Any],
) -> None:
    timers = http_session["timers"]

    assert timers["paused"] is False
    assert timers["closes_at"] is None
    assert seconds_between(http_session["started_at"], timers["nudge_at"]) == 20
    assert seconds_between(http_session["started_at"], timers["hand_off_at"]) == 300


async def test_pausing_stops_every_countdown(
    client: httpx.AsyncClient, http_session: dict[str, Any]
) -> None:
    response = await client.post(f"/sessions/{http_session['session_id']}/pause")

    assert response.status_code == 200
    timers = response.json()["timers"]
    assert timers["paused"] is True
    assert timers["paused_at"] is not None
    assert (timers["nudge_at"], timers["closes_at"], timers["hand_off_at"]) == (
        None,
        None,
        None,
    )
    read_back = await client.get(f"/sessions/{http_session['session_id']}")
    assert read_back.json()["timers"]["paused"] is True


async def test_resuming_starts_the_countdowns_again(
    client: httpx.AsyncClient, http_session: dict[str, Any]
) -> None:
    url = f"/sessions/{http_session['session_id']}"
    await client.post(f"{url}/pause")

    resumed = (await client.post(f"{url}/resume")).json()

    timers = resumed["timers"]
    assert (timers["paused"], timers["paused_at"]) == (False, None)
    assert timers["nudge_at"] is not None
    # The pause was a moment long, so the hand-off is where it was or later.
    assert seconds_between(http_session["started_at"], timers["hand_off_at"]) >= 180


async def test_a_nudged_session_counts_down_to_closing(
    client: httpx.AsyncClient, http_session: dict[str, Any], db_session: AsyncSession
) -> None:
    session_id = uuid.UUID(http_session["session_id"])
    started_at = datetime.fromisoformat(http_session["started_at"])
    await timekeeper.enforce(
        db_session,
        session_id,
        now=started_at + timedelta(seconds=20),
        timers=TIMERS,
        tax_rate=TAX,
    )
    await db_session.commit()

    timers = (await client.get(f"/sessions/{session_id}")).json()["timers"]

    assert timers["nudge_at"] is None
    assert seconds_between(http_session["started_at"], timers["closes_at"]) == 40


async def test_a_session_that_has_ended_has_no_countdowns(
    client: httpx.AsyncClient, http_session: dict[str, Any]
) -> None:
    session_id = http_session["session_id"]
    await client.post(
        f"/sessions/{session_id}/cart/lines", json={"item_code": "cookie"}
    )
    await client.post(f"/sessions/{session_id}/order")

    ended = (await client.get(f"/sessions/{session_id}")).json()

    assert ended["state"] == "submitted"
    assert ended["timers"] == {
        "paused": False,
        "paused_at": None,
        "paused_seconds": 0,
        "nudge_at": None,
        "closes_at": None,
        "hand_off_at": None,
    }


async def test_pausing_a_session_that_has_ended_is_a_conflict(
    client: httpx.AsyncClient, http_session: dict[str, Any]
) -> None:
    session_id = http_session["session_id"]
    await client.post(
        f"/sessions/{session_id}/cart/lines", json={"item_code": "cookie"}
    )
    await client.post(f"/sessions/{session_id}/order")

    for action in ("pause", "resume"):
        response = await client.post(f"/sessions/{session_id}/{action}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_ended"


async def test_pausing_an_unknown_session_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    for action in ("pause", "resume"):
        response = await client.post(f"/sessions/{uuid.uuid4()}/{action}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"


async def test_the_timer_intervals_come_from_the_settings(
    app: FastAPI, client: httpx.AsyncClient, menu: None
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        silence_nudge_seconds=5,
        session_max_seconds=60,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )

    opened = (await client.post("/sessions")).json()

    assert seconds_between(opened["started_at"], opened["timers"]["nudge_at"]) == 5
    assert seconds_between(opened["started_at"], opened["timers"]["hand_off_at"]) == 60


async def test_a_message_after_five_minutes_gets_the_handoff_reply(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession, menu: None
) -> None:
    """End to end through the API, with the scripted demo model standing in:
    it is never asked, because the limit is checked first."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        agent_model="demo",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    opened = (await client.post("/sessions")).json()
    await db_session.execute(
        update(CustomerSession).values(
            started_at=CustomerSession.started_at - timedelta(seconds=301)
        )
    )
    await db_session.commit()

    reply = (
        await client.post(
            f"/sessions/{opened['session_id']}/messages", json={"text": "Hi"}
        )
    ).json()

    assert reply["reply"] == HANDING_OFF[Language.EN]
    assert reply["cart"]["state"] == "handed_off"
    assert reply["tools_used"] == []
    assert STILL_THERE[Language.EN] not in reply["reply"]
