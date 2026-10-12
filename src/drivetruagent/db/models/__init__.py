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

__all__ = [
    "MenuCategory",
    "MenuItem",
    "MenuItemAlias",
    "Option",
    "OptionGroup",
]
