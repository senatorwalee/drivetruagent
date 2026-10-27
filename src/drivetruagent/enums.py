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


class HandoffStatus(StrEnum):
    """Where a session handed to a person has got to."""

    # Waiting for someone to pick it up.
    OPEN = "open"
    # Someone has said they are dealing with it.
    ACKNOWLEDGED = "acknowledged"
    # Finished, one way or the other. It cannot change again.
    RESOLVED = "resolved"


class HandoffOutcome(StrEnum):
    """How a handed-off session was finished."""

    # The cart was placed as an order, as it stood.
    ORDER_PLACED = "order_placed"
    # Nothing was ordered. The stock it held was released.
    CANCELLED = "cancelled"


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
    # A customer message the guardrail flagged: off topic, abuse, or an
    # attempt to get round the agent's rules. Messages that pass are not logged.
    GUARDRAIL = "guardrail"


class Allergen(StrEnum):
    """Health Canada's priority food allergens, with gluten listed apart from
    wheat because a customer avoiding gluten asks about it by that name."""

    PEANUT = "peanut"
    TREE_NUT = "tree_nut"
    SESAME = "sesame"
    MILK = "milk"
    EGG = "egg"
    FISH = "fish"
    # Crustaceans and molluscs.
    SHELLFISH = "shellfish"
    SOY = "soy"
    WHEAT = "wheat"
    GLUTEN = "gluten"
    MUSTARD = "mustard"
    SULPHITE = "sulphite"


class AllergenLevel(StrEnum):
    # It is an ingredient.
    CONTAINS = "contains"
    # Not an ingredient, but cross-contact is possible: a shared fryer.
    MAY_CONTAIN = "may_contain"


class Diet(StrEnum):
    """Religious dietary rules a guest may ask about."""

    HALAL = "halal"
    KOSHER = "kosher"


class DietStatus(StrEnum):
    # Holds a certificate from a recognised body.
    CERTIFIED = "certified"
    # Nothing the diet forbids, and prepared apart, but not certified. The
    # agent says exactly that, never just "halal".
    SUITABLE = "suitable"


class Channel(StrEnum):
    """How a customer reached the agent."""

    # At the speaker in a drive-through lane.
    DRIVE_THRU = "drive_thru"
    # On the phone, ordering for pickup.
    PHONE = "phone"


class PickupStatus(StrEnum):
    """Whether a phone order was collected. Only staff mark it."""

    # Placed, and not yet marked either way.
    WAITING = "waiting"
    PICKED_UP = "picked_up"
    # Never collected. Counts against the number it was ordered from.
    NO_SHOW = "no_show"


class CallerStatus(StrEnum):
    """What staff have decided about a phone number. Only staff change it."""

    NORMAL = "normal"
    # Treated as a number with no-shows: big orders go to a person.
    FLAGGED = "flagged"
    # Calls from it are not taken.
    BLOCKED = "blocked"


class SummaryAuthor(StrEnum):
    """Who wrote a session summary's words. The facts are always code's."""

    # The model wrote them, and they passed the checks.
    MODEL = "model"
    # Fixed wording filled in from the facts: no model, or its words failed.
    TEMPLATE = "template"


class Mood(StrEnum):
    """How the customer came across, as the model judged it."""

    PLEASED = "pleased"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"


class KnowledgeAudience(StrEnum):
    """Who a knowledge document may be shown to."""

    # The customer-facing agent may search it.
    CUSTOMER = "customer"
    # Staff only: procedures, contacts. The agent can never return it.
    STAFF = "staff"


class KnowledgeType(StrEnum):
    INGREDIENTS = "ingredients"
    PREPARATION = "preparation"
    NUTRITION = "nutrition"
    # Vegetarian, vegan, halal and the like.
    DIETS = "diets"
    PROCEDURE = "procedure"


# A session in one of these states is still in progress. Every other state is
# final: the session has ended and can never change again.
ACTIVE_SESSION_STATES = frozenset(
    {SessionState.GREETING, SessionState.ORDERING, SessionState.CONFIRMING}
)
FINAL_SESSION_STATES = frozenset(SessionState) - ACTIVE_SESSION_STATES
