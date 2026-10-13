"""Table models.

Every model module must be imported here. Alembic imports this package to learn
which tables exist, so a model that is not imported here is invisible to it.
"""

from drivetruagent.db.models.catalog import (
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    Option,
    OptionGroup,
)
from drivetruagent.db.models.events import AgentEvent
from drivetruagent.db.models.inventory import (
    IdempotencyRecord,
    Inventory,
    Reservation,
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

__all__ = [
    "AgentEvent",
    "Cart",
    "CartLine",
    "CartLineOption",
    "ComboRule",
    "ComboRuleSlot",
    "CustomerSession",
    "IdempotencyRecord",
    "Inventory",
    "MenuCategory",
    "MenuItem",
    "MenuItemAlias",
    "Option",
    "OptionGroup",
    "Order",
    "OrderDiscount",
    "OrderLine",
    "OrderLineOption",
    "PendingAction",
    "Reservation",
    "SessionSuggestion",
    "Special",
]
