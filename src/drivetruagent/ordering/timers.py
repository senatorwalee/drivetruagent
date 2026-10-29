"""What a session's clocks say is due. Pure logic: no database, no real clock.

A session has two timers. Silence: if nobody has said anything for a while,
the customer is asked whether they are still there, and if the silence goes
on the session is closed. Length: a session that has run too long is handed
to a person. Both are worked out here from a few timestamps and the time
now, which the caller supplies. Nothing in the app sleeps or counts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class TimerAction(StrEnum):
    NONE = "none"
    # Ask the customer whether they are still there.
    NUDGE = "nudge"
    # Nobody answered. Close the session and free what it was holding.
    ABANDON = "abandon"
    # The session has run too long. A person takes over.
    HAND_OFF = "hand_off"


@dataclass(frozen=True)
class TimerSettings:
    # Silence before the customer is asked whether they are still there.
    nudge_after: timedelta = timedelta(seconds=20)
    # Further silence, after asking, before the session is closed.
    close_after: timedelta = timedelta(seconds=20)
    # How long a session may run before a person takes over.
    max_duration: timedelta = timedelta(minutes=5)
    # A turn that started longer ago than this is taken to have crashed, and
    # no longer holds the clocks. It must outlast the slowest real turn.
    turn_grace: timedelta = timedelta(seconds=60)


@dataclass(frozen=True)
class SessionClock:
    """The timestamps a session keeps. See the columns on `sessions`."""

    started_at: datetime
    last_activity_at: datetime
    nudged_at: datetime | None = None
    turn_started_at: datetime | None = None
    paused_at: datetime | None = None
    paused_seconds: int = 0

    @property
    def paused(self) -> bool:
        return self.paused_at is not None

    def mid_turn(self, now: datetime, settings: TimerSettings) -> bool:
        """True while the agent is working on a reply."""
        started = self.turn_started_at
        return started is not None and now - started < settings.turn_grace

    def running_time(self, now: datetime) -> timedelta:
        """How long the session has run, leaving out time spent paused."""
        until = self.paused_at or now
        return until - self.started_at - timedelta(seconds=self.paused_seconds)


@dataclass(frozen=True)
class Deadlines:
    """When each timer will next fire, if nothing else happens. None means
    that timer is not running."""

    nudge_at: datetime | None = None
    closes_at: datetime | None = None
    hand_off_at: datetime | None = None


def due_action(
    clock: SessionClock, now: datetime, settings: TimerSettings
) -> TimerAction:
    """What should happen to a session in progress, right now.

    The order of the checks is the priority. Nothing fires while the session
    is paused or the agent is mid-turn. After that, a customer who has not
    answered the nudge is gone, and that comes before the length limit: if
    nobody is there, there is nobody to hand to a person.
    """
    if clock.paused or clock.mid_turn(now, settings):
        return TimerAction.NONE
    if clock.nudged_at is not None and now - clock.nudged_at >= settings.close_after:
        return TimerAction.ABANDON
    if clock.running_time(now) >= settings.max_duration:
        return TimerAction.HAND_OFF
    if clock.nudged_at is None and now - clock.last_activity_at >= settings.nudge_after:
        return TimerAction.NUDGE
    return TimerAction.NONE


def deadlines(clock: SessionClock, now: datetime, settings: TimerSettings) -> Deadlines:
    """The same rules as `due_action`, as times a screen can count down to."""
    if clock.paused:
        return Deadlines()
    hand_off_at = (
        clock.started_at
        + timedelta(seconds=clock.paused_seconds)
        + settings.max_duration
    )
    if clock.mid_turn(now, settings):
        # Silence is not being measured while the agent is replying.
        return Deadlines(hand_off_at=hand_off_at)
    if clock.nudged_at is not None:
        return Deadlines(
            closes_at=clock.nudged_at + settings.close_after, hand_off_at=hand_off_at
        )
    return Deadlines(
        nudge_at=clock.last_activity_at + settings.nudge_after, hand_off_at=hand_off_at
    )
