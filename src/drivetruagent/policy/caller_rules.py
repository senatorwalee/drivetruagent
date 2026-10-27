"""What a phone number's history means for its next call. Pure logic.

Staff decide two things about a number: flag it, or block it. Everything
else is worked out from what staff have marked, whenever it is needed, and
never stored as a verdict: a number with two no-shows is flagged while they
are recent, and stops being flagged when they are old.

Nothing here reaches the model. The agent is never told a caller's standing;
it only meets its consequences, as a rule's decision or a fixed sentence.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from drivetruagent.enums import CallerStatus


class Standing(StrEnum):
    """How much a call can be trusted with."""

    # Nothing against the number.
    NORMAL = "normal"
    # Recent no-shows, or flagged by staff: big orders go to a person.
    FLAGGED = "flagged"
    # Staff blocked it. Calls are not taken.
    BLOCKED = "blocked"
    # The number was withheld, so there is no history to go on. Treated like
    # a flagged number for big orders.
    UNKNOWN = "unknown"


class TurnAway(StrEnum):
    """Why a call is not taken."""

    BLOCKED = "blocked"
    # Called again and again without ordering.
    TOO_MANY_CALLS = "too_many_calls"


# Said by code, word for word, before hanging up. The caller is not told
# more than that: not the number of calls, nor that staff blocked them.
TURN_AWAY_SENTENCES = {
    TurnAway.BLOCKED: (
        "Sorry, we can't take phone orders from this number. Please order in person."
    ),
    TurnAway.TOO_MANY_CALLS: (
        "Sorry, we're unable to take more calls from this number right now. "
        "Please try again later."
    ),
}


@dataclass(frozen=True)
class CallerLimits:
    """The numbers behind the standing rules. They come from settings."""

    # This many no-shows within the window flag a number.
    flag_after_no_shows: int = 2
    no_show_window: timedelta = timedelta(days=90)
    # An order from a flagged or withheld number totalling more than this
    # goes to a person.
    large_order_cents: int = 4000
    # This many calls within the window, none of them ending in an order,
    # and the next call is turned away.
    repeat_call_limit: int = 5
    repeat_call_window: timedelta = timedelta(minutes=60)


@dataclass(frozen=True)
class CallerCounts:
    """What is known about a number, counted over the limits' windows."""

    status: CallerStatus
    recent_no_shows: int
    # Calls within the repeat-call window that did not end in an order.
    recent_calls_without_order: int


def standing(counts: CallerCounts | None, limits: CallerLimits) -> Standing:
    """A number's standing; `counts` None: the number was withheld."""
    if counts is None:
        return Standing.UNKNOWN
    if counts.status is CallerStatus.BLOCKED:
        return Standing.BLOCKED
    if (
        counts.status is CallerStatus.FLAGGED
        or counts.recent_no_shows >= limits.flag_after_no_shows
    ):
        return Standing.FLAGGED
    return Standing.NORMAL


def turn_away(counts: CallerCounts | None, limits: CallerLimits) -> TurnAway | None:
    """Why a call from this number should not be taken, or None to take it.

    A withheld number is always taken: there is nothing to count it by.
    """
    if counts is None:
        return None
    if standing(counts, limits) is Standing.BLOCKED:
        return TurnAway.BLOCKED
    if counts.recent_calls_without_order >= limits.repeat_call_limit:
        return TurnAway.TOO_MANY_CALLS
    return None


def needs_person_for_size(
    caller_standing: Standing, total_cents: int, limits: CallerLimits
) -> bool:
    """True if an order this size, from this number, should go to a person."""
    return (
        caller_standing in (Standing.FLAGGED, Standing.UNKNOWN)
        and total_cents > limits.large_order_cents
    )
