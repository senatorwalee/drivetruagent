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


class PendingActionStatus(StrEnum):
    """What became of an action that was held for the customer's confirmation."""

    # Waiting for the customer's answer.
    PENDING = "pending"
    # The customer said yes and the action ran.
    CONFIRMED = "confirmed"
    # The customer said no, or the action could no longer run.
    CANCELLED = "cancelled"
    # The cart changed or another action was proposed before an answer came.
    SUPERSEDED = "superseded"
    # The customer said yes, and the order was handed to a person.
    ESCALATED = "escalated"


class ReservationStatus(StrEnum):
    """What became of stock a session was holding."""

    # Held for a session that is still ordering.
    ACTIVE = "active"
    # The order was placed and the stock was taken.
    COMMITTED = "committed"
    # Given back: the line was removed, the session ended, or the hold expired.
    RELEASED = "released"


class DiscountKind(StrEnum):
    """Why an amount came off an order."""

    # Items bought together, such as a burger, a side and a drink.
    COMBO = "combo"
    # An item that is cheaper today.
    SPECIAL = "special"


class SuggestionStatus(StrEnum):
    """Whether a suggestion made to a session can still be made again."""

    # Being offered in the customer turn that is running now.
    OPEN = "open"
    # The customer has spoken since. It is never offered to them again.
    CLOSED = "closed"


class AgentEventKind(StrEnum):
    """What one row of the agent's event log records."""

    USER_MESSAGE = "user_message"
    # One request to the model: which model, how long it took, token counts.
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ASSISTANT_MESSAGE = "assistant_message"
    # Something the backend did because of a clock, not because of the model:
    # asked if the customer was still there, or ended the session.
    TIMER = "timer"


# A session in one of these states is still in progress. Every other state is
# final: the session has ended and can never change again.
ACTIVE_SESSION_STATES = frozenset(
    {SessionState.GREETING, SessionState.ORDERING, SessionState.CONFIRMING}
)
FINAL_SESSION_STATES = frozenset(SessionState) - ACTIVE_SESSION_STATES
