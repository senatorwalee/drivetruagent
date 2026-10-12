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


class AgentEventKind(StrEnum):
    """What one row of the agent's event log records."""

    USER_MESSAGE = "user_message"
    # One request to the model: which model, how long it took, token counts.
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ASSISTANT_MESSAGE = "assistant_message"


# A session in one of these states is still in progress. Every other state is
# final: the session has ended and can never change again.
ACTIVE_SESSION_STATES = frozenset(
    {SessionState.GREETING, SessionState.ORDERING, SessionState.CONFIRMING}
)
FINAL_SESSION_STATES = frozenset(SessionState) - ACTIVE_SESSION_STATES
