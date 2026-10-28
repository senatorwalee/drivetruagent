"""Reads how the restaurant has reviewed its items for a diet such as halal:
certified, suitable but not certified, or not suitable, each with why. An
item that is not listed has not been reviewed, and nothing about it may be
claimed either way."""

import uuid
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import DietSuitability, MenuCategory, MenuItem
from drivetruagent.enums import Diet, DietStatus, Language
from drivetruagent.ordering.cart import get_cart
from drivetruagent.ordering.errors import UnknownItem
from drivetruagent.ordering.inventory import sold_out_item_ids
from drivetruagent.ordering.views import localize


class ItemDiet(BaseModel):
    item_code: str
    item_name: str
    category_code: str
    # The cart line this is about, when the cart was checked.
    line_no: int | None = None
    # None: not reviewed. Nothing about it may be claimed, either way.
    status: DietStatus | None
    # Why it is or isn't suitable, in words a guest can hear.
    note: str | None = None


class DietOption(BaseModel):
    item_code: str
    item_name: str
    status: DietStatus
    note: str


async def item_diet(
    db: AsyncSession,
    item_code: str,
    diet: Diet,
    language: Language,
    *,
    line_no: int | None = None,
) -> ItemDiet:
    """One item's standing for a diet. Raises UnknownItem."""
    row = (
        await db.execute(
            select(MenuItem, MenuCategory.code)
            .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
            .where(MenuItem.code == item_code)
        )
    ).one_or_none()
    if row is None:
        raise UnknownItem(item_code)
    item, category_code = row
    entry = (
        await db.execute(
            select(DietSuitability).where(
                DietSuitability.item_id == item.id, DietSuitability.diet == diet
            )
        )
    ).scalar_one_or_none()
    return ItemDiet(
        item_code=item.code,
        item_name=localize(item.name_i18n, language),
        category_code=category_code,
        line_no=line_no,
        status=entry.status if entry else None,
        note=localize(entry.note_i18n, language) if entry else None,
    )


async def cart_diet(
    db: AsyncSession,
    session_id: uuid.UUID,
    diet: Diet,
    language: Language,
    *,
    tax_rate: Decimal,
) -> list[ItemDiet]:
    """Every line in the session's cart, for a diet."""
    cart = await get_cart(db, session_id, tax_rate=tax_rate)
    return [
        await item_diet(db, line.item_code, diet, language, line_no=line.line_no)
        for line in cart.lines
    ]


async def suitable_items(
    db: AsyncSession,
    diet: Diet,
    language: Language,
    *,
    category_code: str | None = None,
    limit: int = 3,
) -> list[DietOption]:
    """In-stock items listed as certified or suitable for a diet, certified
    first, then in menu order. From one category when given."""
    statement = (
        select(MenuItem, DietSuitability)
        .join(DietSuitability, DietSuitability.item_id == MenuItem.id)
        .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
        .where(
            MenuItem.is_active,
            DietSuitability.diet == diet,
            DietSuitability.status.in_((DietStatus.CERTIFIED, DietStatus.SUITABLE)),
        )
        .order_by(MenuCategory.sort_order, MenuItem.sort_order)
    )
    if category_code is not None:
        statement = statement.where(MenuCategory.code == category_code)
    sold_out = await sold_out_item_ids(db)
    rows = [
        (item, entry)
        for item, entry in (await db.execute(statement)).all()
        if item.id not in sold_out
    ]
    rows.sort(key=lambda row: row[1].status is not DietStatus.CERTIFIED)
    return [
        DietOption(
            item_code=item.code,
            item_name=localize(item.name_i18n, language),
            status=entry.status,
            note=localize(entry.note_i18n, language),
        )
        for item, entry in rows[:limit]
    ]


class MenuDiet(BaseModel):
    """The whole menu for a diet, for "are you halal?"."""

    # Suitable or certified items in stock, by part of the menu, in menu order.
    suitable: dict[str, list[str]]
    # What was reviewed and isn't suitable, with why, in menu order.
    not_suitable: list[DietOption]


async def menu_diet(db: AsyncSession, diet: Diet, language: Language) -> MenuDiet:
    rows = (
        await db.execute(
            select(MenuItem, MenuCategory, DietSuitability)
            .join(DietSuitability, DietSuitability.item_id == MenuItem.id)
            .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
            .where(MenuItem.is_active, DietSuitability.diet == diet)
            .order_by(MenuCategory.sort_order, MenuItem.sort_order)
        )
    ).all()
    sold_out = await sold_out_item_ids(db)
    suitable: dict[str, list[str]] = {}
    not_suitable: list[DietOption] = []
    for item, category, entry in rows:
        name = localize(item.name_i18n, language)
        if entry.status is DietStatus.NOT_SUITABLE:
            not_suitable.append(
                DietOption(
                    item_code=item.code,
                    item_name=name,
                    status=entry.status,
                    note=localize(entry.note_i18n, language),
                )
            )
        elif item.id not in sold_out:
            suitable.setdefault(localize(category.name_i18n, language), []).append(name)
    return MenuDiet(suitable=suitable, not_suitable=not_suitable)


async def any_certified(db: AsyncSession, diet: Diet) -> bool:
    """Whether anything on the menu is certified for the diet."""
    found = await db.scalar(
        select(DietSuitability.id)
        .where(
            DietSuitability.diet == diet,
            DietSuitability.status == DietStatus.CERTIFIED,
        )
        .limit(1)
    )
    return found is not None
