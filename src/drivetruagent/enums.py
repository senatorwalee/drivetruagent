"""Fixed sets of values used across the app and stored in the database as text."""

from enum import StrEnum


class Language(StrEnum):
    EN = "en"
    FR = "fr"


class SessionState(StrEnum):
    """Where one customer's visit to the speaker has got to."""

    # Opened; the customer has not ordered anything yet.
    GREETING = "greeting"
    # Building the cart.
    ORDERING = "ordering"
    # The agent has asked whether the order is correct.
    CONFIRMING = "confirming"
    # The order was placed.
    SUBMITTED = "submitted"
    # Passed to a human.
    HANDED_OFF = "handed_off"
    # Silence timeout, or the customer left.
    ABANDONED = "abandoned"
    # Ended for abuse.
    TERMINATED = "terminated"


# A session in one of these states is still in progress. Every other state is
# final: the session has ended and can never change again.
ACTIVE_SESSION_STATES = frozenset(
    {SessionState.GREETING, SessionState.ORDERING, SessionState.CONFIRMING}
)
FINAL_SESSION_STATES = frozenset(SessionState) - ACTIVE_SESSION_STATES
