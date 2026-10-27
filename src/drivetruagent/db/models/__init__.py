"""Table models.

Every model module must be imported here. Alembic imports this package to learn
which tables exist, so a model that is not imported here is invisible to it.
"""

from drivetruagent.db.models.callers import Caller, CallerStatusChange
from drivetruagent.db.models.catalog import (
    AllergenDeclaration,
    DietSuitability,
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    MenuItemPhoto,
    Option,
    OptionGroup,
)
from drivetruagent.db.models.events import AgentEvent
from drivetruagent.db.models.handoff import Handoff
from drivetruagent.db.models.inventory import (
    IdempotencyRecord,
    Inventory,
    Reservation,
)
from drivetruagent.db.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
)
from drivetruagent.db.models.offers import (
    ComboRule,
    ComboRuleSlot,
    SessionSuggestion,
    Special,
)
from drivetruagent.db.models.ordering import (
    Cart,
    CartLine,
    CartLineOption,
    CustomerSession,
)
from drivetruagent.db.models.orders import (
    Order,
    OrderDiscount,
    OrderLine,
    OrderLineOption,
)
from drivetruagent.db.models.policy import PendingAction
from drivetruagent.db.models.summaries import SessionSummary

__all__ = [
    "AgentEvent",
    "AllergenDeclaration",
    "Caller",
    "CallerStatusChange",
    "Cart",
    "CartLine",
    "CartLineOption",
    "ComboRule",
    "ComboRuleSlot",
    "CustomerSession",
    "DietSuitability",
    "Handoff",
    "IdempotencyRecord",
    "Inventory",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeEmbedding",
    "MenuCategory",
    "MenuItem",
    "MenuItemAlias",
    "MenuItemPhoto",
    "Option",
    "OptionGroup",
    "Order",
    "OrderDiscount",
    "OrderLine",
    "OrderLineOption",
    "PendingAction",
    "Reservation",
    "SessionSuggestion",
    "SessionSummary",
    "Special",
]
