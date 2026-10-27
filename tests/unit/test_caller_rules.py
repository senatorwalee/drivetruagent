"""What a phone number's history means for its next call."""

import pytest

from drivetruagent.enums import CallerStatus
from drivetruagent.policy.caller_rules import (
    CallerCounts,
    CallerLimits,
    Standing,
    TurnAway,
    needs_person_for_size,
    standing,
    turn_away,
)

LIMITS = CallerLimits()


def counts(
    status: CallerStatus = CallerStatus.NORMAL,
    no_shows: int = 0,
    calls_without_order: int = 0,
) -> CallerCounts:
    return CallerCounts(
        status=status,
        recent_no_shows=no_shows,
        recent_calls_without_order=calls_without_order,
    )


# --- Standing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        (counts(), Standing.NORMAL),
        (counts(no_shows=1), Standing.NORMAL),
        (counts(no_shows=2), Standing.FLAGGED),
        (counts(no_shows=7), Standing.FLAGGED),
        (counts(CallerStatus.FLAGGED), Standing.FLAGGED),
        (counts(CallerStatus.BLOCKED), Standing.BLOCKED),
        # Blocking outranks everything else.
        (counts(CallerStatus.BLOCKED, no_shows=3), Standing.BLOCKED),
        (None, Standing.UNKNOWN),
    ],
)
def test_standing(caller: CallerCounts | None, expected: Standing) -> None:
    assert standing(caller, LIMITS) is expected


def test_the_no_show_threshold_is_a_setting() -> None:
    strict = CallerLimits(flag_after_no_shows=1)

    assert standing(counts(no_shows=1), strict) is Standing.FLAGGED


def test_clearing_a_number_does_not_clear_its_recent_no_shows() -> None:
    """Staff can unflag a number they flagged; the no-shows still count until
    they are old."""
    assert standing(counts(CallerStatus.NORMAL, no_shows=2), LIMITS) is Standing.FLAGGED


# --- Turning calls away -----------------------------------------------------


@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        (counts(), None),
        (counts(calls_without_order=4), None),
        (counts(calls_without_order=5), TurnAway.TOO_MANY_CALLS),
        (counts(CallerStatus.BLOCKED), TurnAway.BLOCKED),
        (counts(CallerStatus.BLOCKED, calls_without_order=9), TurnAway.BLOCKED),
        # Flagged numbers still get through; only their big orders are capped.
        (counts(CallerStatus.FLAGGED, no_shows=5), None),
        # A withheld number has nothing to count it by.
        (None, None),
    ],
)
def test_turn_away(caller: CallerCounts | None, expected: TurnAway | None) -> None:
    assert turn_away(caller, LIMITS) is expected


# --- Big orders -------------------------------------------------------------


@pytest.mark.parametrize(
    ("caller_standing", "total_cents", "expected"),
    [
        (Standing.FLAGGED, 4001, True),
        (Standing.UNKNOWN, 4001, True),
        # The limit itself is still fine.
        (Standing.FLAGGED, 4000, False),
        (Standing.FLAGGED, 1500, False),
        (Standing.NORMAL, 25000, False),
    ],
)
def test_big_orders_from_untrusted_numbers_need_a_person(
    caller_standing: Standing, total_cents: int, expected: bool
) -> None:
    assert needs_person_for_size(caller_standing, total_cents, LIMITS) is expected
