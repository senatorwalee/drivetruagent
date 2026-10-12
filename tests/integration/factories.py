"""Helpers that create valid rows, so each test changes only the thing it checks.

Every helper flushes, which sends the INSERT to the database and makes any
constraint violation surface at that line.
"""

import itertools
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import MenuCategory, MenuItem, Option, OptionGroup

_counter = itertools.count(1)


def unique_code(prefix: str) -> str:
    return f"{prefix}_{next(_counter)}"


async def make_category(session: AsyncSession, **overrides: Any) -> MenuCategory:
    values: dict[str, Any] = {
        "code": unique_code("category"),
        "name_i18n": {"en": "Sides", "fr": "Accompagnements"},
    }
    category = MenuCategory(**values | overrides)
    session.add(category)
    await session.flush()
    return category


async def make_item(
    session: AsyncSession, category: MenuCategory | None = None, **overrides: Any
) -> MenuItem:
    category = category or await make_category(session)
    values: dict[str, Any] = {
        "code": unique_code("item"),
        "category_id": category.id,
        "name_i18n": {"en": "Fries", "fr": "Frites"},
        "base_price_cents": 249,
    }
    item = MenuItem(**values | overrides)
    session.add(item)
    await session.flush()
    return item


async def make_group(
    session: AsyncSession, item: MenuItem | None = None, **overrides: Any
) -> OptionGroup:
    item = item or await make_item(session)
    values: dict[str, Any] = {
        "item_id": item.id,
        "code": unique_code("group"),
        "name_i18n": {"en": "Size", "fr": "Format"},
        "min_select": 1,
        "max_select": 1,
    }
    group = OptionGroup(**values | overrides)
    session.add(group)
    await session.flush()
    return group


async def make_option(
    session: AsyncSession, group: OptionGroup | None = None, **overrides: Any
) -> Option:
    group = group or await make_group(session)
    values: dict[str, Any] = {
        "option_group_id": group.id,
        "code": unique_code("option"),
        "name_i18n": {"en": "Small", "fr": "Petit"},
    }
    option = Option(**values | overrides)
    session.add(option)
    await session.flush()
    return option
