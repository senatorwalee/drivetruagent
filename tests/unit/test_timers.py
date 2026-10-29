"""What a session's clocks say is due. No database, and no real clock: every
test says what time it is.

Settings used throughout: asked after 20 seconds of silence, closed 20 seconds
after that, handed off at three minutes.
"""

from datetime import UTC, datetime, timedelta

import pytest

from drivetruagent.ordering.timers import (
    Deadlines,
    SessionClock,
    TimerAction,
    TimerSettings,
    deadlines,
    due_action,
)

# The tests count in a three-minute limit, whatever the default.
SETTINGS = TimerSettings(max_duration=timedelta(minutes=3))
START = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A time, as seconds after the session started."""
    return START + timedelta(seconds=seconds)


def clock(
    *,
    last_activity: float = 0,
    nudged: float | None = None,
    turn_started: float | None = None,
    paused: float | None = None,
    paused_seconds: int = 0,
) -> SessionClock:
    return SessionClock(
        started_at=START,
        last_activity_at=at(last_activity),
        nudged_at=None if nudged is None else at(nudged),
        turn_started_at=None if turn_started is None else at(turn_started),
        paused_at=None if paused is None else at(paused),
        paused_seconds=paused_seconds,
    )


def due(session_clock: SessionClock, seconds: float) -> TimerAction:
    return due_action(session_clock, at(seconds), SETTINGS)


def test_the_defaults() -> None:
    assert TimerSettings().nudge_after == timedelta(seconds=20)
    # The brief asked for three minutes; five gives a real order room.
    assert TimerSettings().max_duration == timedelta(minutes=5)


# --- Silence ----------------------------------------------------------------


def test_nothing_is_due_in_a_session_that_has_just_started() -> None:
    assert due(clock(), 0) is TimerAction.NONE


def test_the_nudge_is_due_at_twenty_seconds_of_silence_exactly() -> None:
    assert due(clock(), 19.9) is TimerAction.NONE
    assert due(clock(), 20) is TimerAction.NUDGE
    assert due(clock(), 35) is TimerAction.NUDGE


def test_silence_is_measured_from_the_last_activity_not_the_start() -> None:
    busy = clock(last_activity=50)

    assert due(busy, 69) is TimerAction.NONE
    assert due(busy, 70) is TimerAction.NUDGE


def test_a_customer_is_asked_only_once_per_silence() -> None:
    asked = clock(nudged=20)

    assert due(asked, 21) is TimerAction.NONE
    assert due(asked, 39.9) is TimerAction.NONE


def test_no_answer_twenty_seconds_after_the_nudge_abandons() -> None:
    asked = clock(nudged=20)

    assert due(asked, 40) is TimerAction.ABANDON
    assert due(asked, 100) is TimerAction.ABANDON


def test_the_wait_after_a_nudge_runs_from_when_it_was_said() -> None:
    """A sweep that ran late still gives the customer their full 20 seconds
    to answer."""
    asked_late = clock(nudged=31)

    assert due(asked_late, 50) is TimerAction.NONE
    assert due(asked_late, 51) is TimerAction.ABANDON


def test_an_answer_starts_the_silence_again() -> None:
    """Answering clears the nudge and counts as activity."""
    answered = clock(last_activity=30)

    assert due(answered, 49) is TimerAction.NONE
    assert due(answered, 50) is TimerAction.NUDGE


# --- Length -----------------------------------------------------------------


def test_a_session_is_handed_off_at_three_minutes_exactly() -> None:
    chatty = clock(last_activity=175)

    assert due(chatty, 179.9) is TimerAction.NONE
    assert due(chatty, 180) is TimerAction.HAND_OFF
    assert due(chatty, 400) is TimerAction.HAND_OFF


def test_the_length_limit_comes_before_a_first_nudge() -> None:
    """Silent since 2:45 and now at 3:05: both are due. A person is more use
    than a question."""
    assert due(clock(last_activity=165), 185) is TimerAction.HAND_OFF


def test_an_unanswered_nudge_comes_before_the_length_limit() -> None:
    """Nobody is there. There is nobody to hand to a person."""
    assert due(clock(last_activity=150, nudged=170), 190) is TimerAction.ABANDON


def test_a_nudged_session_can_still_reach_the_length_limit_first() -> None:
    """Asked at 2:50; three minutes arrives before their 20 seconds are up."""
    assert due(clock(last_activity=150, nudged=170), 180) is TimerAction.HAND_OFF


# --- Paused -----------------------------------------------------------------


@pytest.mark.parametrize("seconds", [10, 30, 60, 200, 100_000])
def test_nothing_is_ever_due_while_paused(seconds: float) -> None:
    assert due(clock(paused=5), seconds) is TimerAction.NONE
    assert due(clock(paused=5, nudged=4), seconds) is TimerAction.NONE


def test_time_spent_paused_does_not_count_toward_the_length() -> None:
    """Paused for 100 seconds in all: three minutes of running time is
    reached at 280 seconds on the wall."""
    resumed = clock(last_activity=275, paused_seconds=100)

    assert due(resumed, 279) is TimerAction.NONE
    assert due(resumed, 280) is TimerAction.HAND_OFF


def test_running_time_stops_at_the_pause() -> None:
    paused = clock(paused=60, paused_seconds=10)

    assert paused.running_time(at(60)) == timedelta(seconds=50)
    assert paused.running_time(at(5000)) == timedelta(seconds=50)


def test_running_time_without_a_pause_is_time_since_the_start() -> None:
    assert clock().running_time(at(42)) == timedelta(seconds=42)


# --- Mid-turn ---------------------------------------------------------------


def test_nothing_is_due_while_the_agent_is_replying() -> None:
    """The model has been thinking for 25 seconds. That is not the customer
    being silent."""
    thinking = clock(last_activity=10, turn_started=10)

    assert due(thinking, 35) is TimerAction.NONE


def test_a_turn_under_way_is_not_cut_off_by_the_length_limit() -> None:
    """ "Yes, that's right" at 2:58. The order is placed, not handed off."""
    confirming = clock(last_activity=178, turn_started=178)

    assert due(confirming, 181) is TimerAction.NONE


def test_once_the_turn_ends_the_length_limit_applies() -> None:
    assert due(clock(last_activity=183), 183) is TimerAction.HAND_OFF


def test_a_turn_that_never_finished_does_not_freeze_the_session() -> None:
    """The process died mid-turn. After a minute the marker is ignored."""
    crashed = clock(last_activity=10, turn_started=10)

    assert due(crashed, 69.9) is TimerAction.NONE
    assert due(crashed, 70) is TimerAction.NUDGE
    assert crashed.mid_turn(at(69.9), SETTINGS) is True
    assert crashed.mid_turn(at(70), SETTINGS) is False


# --- Other settings ---------------------------------------------------------


def test_the_intervals_come_from_the_settings() -> None:
    quick = TimerSettings(
        nudge_after=timedelta(seconds=5),
        close_after=timedelta(seconds=3),
        max_duration=timedelta(seconds=60),
    )

    assert due_action(clock(), at(5), quick) is TimerAction.NUDGE
    assert due_action(clock(nudged=5), at(8), quick) is TimerAction.ABANDON
    assert due_action(clock(last_activity=59), at(60), quick) is TimerAction.HAND_OFF


# --- Deadlines --------------------------------------------------------------


def test_a_quiet_session_counts_down_to_the_nudge_and_the_limit() -> None:
    assert deadlines(clock(last_activity=30), at(31), SETTINGS) == Deadlines(
        nudge_at=at(50), closes_at=None, hand_off_at=at(180)
    )


def test_a_nudged_session_counts_down_to_closing() -> None:
    assert deadlines(clock(nudged=20), at(25), SETTINGS) == Deadlines(
        nudge_at=None, closes_at=at(40), hand_off_at=at(180)
    )


def test_a_paused_session_counts_down_to_nothing() -> None:
    assert deadlines(clock(paused=30), at(90), SETTINGS) == Deadlines()


def test_mid_turn_only_the_length_limit_is_shown() -> None:
    assert deadlines(clock(turn_started=10), at(12), SETTINGS) == Deadlines(
        hand_off_at=at(180)
    )


def test_time_paused_pushes_the_hand_off_back() -> None:
    resumed = clock(last_activity=100, paused_seconds=45)

    assert deadlines(resumed, at(100), SETTINGS).hand_off_at == at(225)


SITUATIONS = [
    clock(),
    clock(last_activity=50),
    clock(last_activity=20, nudged=45),
    clock(last_activity=150, nudged=170),
    clock(last_activity=100, paused_seconds=45),
    clock(paused=30),
]


@pytest.mark.parametrize("session_clock", SITUATIONS)
def test_each_deadline_is_the_first_moment_its_action_is_due(
    session_clock: SessionClock,
) -> None:
    """The countdown a screen shows and the rule the backend applies cannot
    drift apart: at every deadline the matching action really is due, and a
    moment earlier it is not."""
    shown = deadlines(session_clock, START, SETTINGS)
    expected = {
        TimerAction.NUDGE: shown.nudge_at,
        TimerAction.ABANDON: shown.closes_at,
        TimerAction.HAND_OFF: shown.hand_off_at,
    }
    first = min((when for when in expected.values() if when), default=None)
    if first is None:
        # Paused: nothing is shown, and nothing is ever due.
        assert due_action(session_clock, at(100_000), SETTINGS) is TimerAction.NONE
        return
    action = next(a for a, when in expected.items() if when == first)

    just_before = due_action(session_clock, first - timedelta(milliseconds=1), SETTINGS)
    on_time = due_action(session_clock, first, SETTINGS)

    assert just_before is TimerAction.NONE
    assert on_time is action
