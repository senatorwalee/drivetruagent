"""Session timers with real concurrency: separate connections, real commits.

These are in a module of their own because they must not share a test with
the rollback-based fixtures: an open transaction there would block the
clean-up here.

Time is still passed in. The only waiting is for the background task to get
its turn on the event loop.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.agent import events, timekeeper
from drivetruagent.agent.timekeeper import SweepReport
from drivetruagent.db.models import Caller, CustomerSession
from drivetruagent.enums import AgentEventKind, Channel
from drivetruagent.ordering.sessions import open_session
from drivetruagent.ordering.timers import TimerSettings

TAX = Decimal("0.13")
TIMERS = TimerSettings()


class Car:
    """One session, and a way to say what time it is for it."""

    def __init__(self, customer_session: CustomerSession) -> None:
        self.id = customer_session.id
        self.started_at = customer_session.started_at

    def at(self, seconds: float) -> datetime:
        """A time, as seconds after this session started."""
        return self.started_at + timedelta(seconds=seconds)


async def sweep(db: AsyncSession, car: Car, seconds: float) -> SweepReport:
    return await timekeeper.sweep(db, now=car.at(seconds), timers=TIMERS, tax_rate=TAX)


async def said(db: AsyncSession, car: Car) -> list[str]:
    """Everything the agent has said, in order."""
    return [
        event.payload["text"]
        for event in await events.load_events(db, car.id)
        if event.kind is AgentEventKind.ASSISTANT_MESSAGE
    ]


type Sessions = async_sessionmaker[AsyncSession]


async def open_committed_car(sessions_factory: Sessions) -> Car:
    async with sessions_factory.begin() as db:
        return Car(await open_session(db))


async def nudges(sessions_factory: Sessions, car: Car) -> list[str]:
    async with sessions_factory() as db:
        return await said(db, car)


async def test_two_sweeps_at_the_same_moment_ask_once(
    committing_sessions: Sessions,
) -> None:
    """Two server processes, both waking on the same second. Each handles the
    session under its lock and decides again once it has it."""
    car = await open_committed_car(committing_sessions)

    async def one_sweep() -> SweepReport:
        async with committing_sessions() as db:
            return await sweep(db, car, 20)

    reports = await asyncio.gather(*(one_sweep() for _ in range(4)))

    assert sorted(report.nudged for report in reports) == [0, 0, 0, 1]
    assert await nudges(committing_sessions, car) == ["Are you still there?"]


class SweepClock:
    """The clock handed to the background task. It is asked the time once per
    sweep, so it also knows how many sweeps have started, and can say when a
    given one has: no polling needed to know a sweep has finished.
    """

    def __init__(self, now: datetime, *, fail_first: int = 0) -> None:
        self.now = now
        self.fail_first = fail_first
        self.calls = 0
        # Set when the sweep after the first successful one starts, which
        # means the first successful one has finished.
        self.one_sweep_done = asyncio.Event()

    def __call__(self) -> datetime:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise RuntimeError("no clock yet")
        if self.calls >= self.fail_first + 2:
            self.one_sweep_done.set()
        return self.now


async def run_until_one_sweep_is_done(
    sessions_factory: Sessions,
    clock: SweepClock,
    *,
    caller_retention: timedelta | None = None,
) -> None:
    task = asyncio.create_task(
        timekeeper.run_forever(
            sessions_factory,
            interval_seconds=0.01,
            timers=TIMERS,
            tax_rate=TAX,
            caller_retention=caller_retention,
            clock=clock,
        )
    )
    async with asyncio.timeout(10):
        await clock.one_sweep_done.wait()
    task.cancel()
    # Cancelling is how the app stops it at shutdown. It must stop, and
    # stop by being cancelled, not by some other error.
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_background_task_sweeps_until_it_is_cancelled(
    committing_sessions: Sessions,
) -> None:
    car = await open_committed_car(committing_sessions)

    await run_until_one_sweep_is_done(committing_sessions, SweepClock(car.at(20)))

    assert await nudges(committing_sessions, car) == ["Are you still there?"]


async def test_the_background_task_survives_a_sweep_that_fails(
    committing_sessions: Sessions,
) -> None:
    """A database blip must not stop timers for the rest of the day."""
    car = await open_committed_car(committing_sessions)
    clock = SweepClock(car.at(20), fail_first=2)

    await run_until_one_sweep_is_done(committing_sessions, clock)

    assert clock.calls >= 4
    assert await nudges(committing_sessions, car) == ["Are you still there?"]


async def test_the_background_task_forgets_numbers_past_their_retention(
    committing_sessions: Sessions,
) -> None:
    async with committing_sessions.begin() as db:
        await open_session(db, channel=Channel.PHONE, caller_phone="+14165550123")
        await open_session(db, channel=Channel.PHONE, caller_phone="+16475550199")
        await db.execute(
            update(Caller)
            .where(Caller.phone_e164 == "+16475550199")
            .values(last_call_at=func.now() - timedelta(days=400))
        )

    await run_until_one_sweep_is_done(
        committing_sessions,
        SweepClock(datetime.now(UTC)),
        caller_retention=timedelta(days=365),
    )

    async with committing_sessions() as db:
        kept = (await db.execute(select(Caller.phone_e164))).scalars().all()
    assert kept == ["+14165550123"]
