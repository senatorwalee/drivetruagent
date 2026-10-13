"""Helpers that create valid rows, so each test changes only the thing it checks.

Every helper flushes, which sends the INSERT to the database and makes any
constraint violation surface at that line.
"""

import itertools
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    Cart,
    CartLine,
    CustomerSession,
    MenuCategory,
    MenuItem,
    Option,
    OptionGroup,
    Order,
    OrderLine,
)
from drivetruagent.enums import Language

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


async def make_session(session: AsyncSession, **overrides: Any) -> CustomerSession:
    """A customer session. Each gets its own lane unless one is given, because
    only one active session is allowed per lane."""
    values: dict[str, Any] = {
        "lane": unique_code("lane"),
        "business_date": date(2026, 10, 12),
    }
    customer_session = CustomerSession(**values | overrides)
    session.add(customer_session)
    await session.flush()
    return customer_session


async def make_cart(
    session: AsyncSession,
    customer_session: CustomerSession | None = None,
    **overrides: Any,
) -> Cart:
    customer_session = customer_session or await make_session(session)
    cart = Cart(**{"session_id": customer_session.id} | overrides)
    session.add(cart)
    await session.flush()
    return cart


async def make_cart_line(
    session: AsyncSession,
    cart: Cart | None = None,
    item: MenuItem | None = None,
    **overrides: Any,
) -> CartLine:
    cart = cart or await make_cart(session)
    item = item or await make_item(session)
    values: dict[str, Any] = {
        "cart_id": cart.id,
        "item_id": item.id,
        "line_no": 1,
        "quantity": 1,
    }
    line = CartLine(**values | overrides)
    session.add(line)
    await session.flush()
    return line


async def make_order(
    session: AsyncSession,
    customer_session: CustomerSession | None = None,
    **overrides: Any,
) -> Order:
    customer_session = customer_session or await make_session(session)
    values: dict[str, Any] = {
        "session_id": customer_session.id,
        "language": Language.EN,
        "subtotal_cents": 1000,
        "tax_cents": 150,
        "total_cents": 1150,
    }
    order = Order(**values | overrides)
    session.add(order)
    await session.flush()
    return order


async def make_order_line(
    session: AsyncSession,
    order: Order | None = None,
    item: MenuItem | None = None,
    **overrides: Any,
) -> OrderLine:
    order = order or await make_order(session)
    item = item or await make_item(session)
    values: dict[str, Any] = {
        "order_id": order.id,
        "line_no": 1,
        "item_id": item.id,
        "item_code": item.code,
        "item_name": "Fries",
        "quantity": 2,
        "unit_price_cents": 249,
        "line_total_cents": 498,
    }
    line = OrderLine(**values | overrides)
    session.add(line)
    await session.flush()
    return line
