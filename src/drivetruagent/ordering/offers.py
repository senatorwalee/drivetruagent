"""Combos and specials as the database holds them, and what has been
suggested to whom.

The arithmetic is in `discounts`; this module reads the rules and keeps the
record that stops a customer being offered the same thing twice.
"""

import uuid
from collections.abc import Mapping
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.cache import TtlCache
from drivetruagent.db import models
from drivetruagent.db.models import (
    ComboRuleSlot,
    CustomerSession,
    MenuCategory,
    MenuItem,
    SessionSuggestion,
)
from drivetruagent.enums import DiscountKind, Language, SuggestionStatus
from drivetruagent.ordering.discounts import (
    ComboRule,
    ComboSlot,
    Special,
    apply_combos,
    find_opening,
)
from drivetruagent.ordering.errors import UnknownSpecial
from drivetruagent.ordering.inventory import sold_out_item_ids
from drivetruagent.ordering.views import (
    DEFAULT_LANGUAGE,
    CategoryRef,
    ComboView,
    SpecialView,
    SuggestionView,
    localize,
)

# The specials running on a date, as plain values that are safe to share.
type SpecialsCache = TtlCache[date, tuple[Special, ...]]


async def load_combo_rules(db: AsyncSession) -> tuple[ComboRule, ...]:
    """The active combos, highest priority first."""
    statement = (
        select(models.ComboRule)
        .where(models.ComboRule.is_active)
        .order_by(models.ComboRule.sort_order, models.ComboRule.code)
        .options(
            selectinload(models.ComboRule.slots).selectinload(ComboRuleSlot.category)
        )
        .execution_options(populate_existing=True)
    )
    return tuple(
        ComboRule(
            code=row.code,
            name=row.name_i18n,
            discount_cents=row.discount_cents,
            slots=tuple(
                ComboSlot(slot.category.code, slot.category.name_i18n)
                for slot in row.slots
            ),
        )
        for row in (await db.execute(statement)).scalars()
        # A rule with no slots would match every cart. It cannot be seeded,
        # but nothing in the database forbids it, so it is ignored here.
        if row.slots
    )


async def list_combos(db: AsyncSession, language: Language) -> list[ComboView]:
    return [
        ComboView(
            code=rule.code,
            name=localize(rule.name, language),
            slots=_category_refs(rule.slots, language),
            saves_cents=rule.discount_cents,
        )
        for rule in await load_combo_rules(db)
    ]


async def load_specials(db: AsyncSession, on: date) -> tuple[Special, ...]:
    """The specials running on a date, highest priority first, from the database.

    Pricing uses this, never the cache: a cart is charged what the database
    says inside its own transaction.
    """
    statement = (
        select(models.Special)
        .join(models.Special.item)
        .where(models.Special.is_active, MenuItem.is_active)
        .order_by(models.Special.sort_order, models.Special.code)
        .options(selectinload(models.Special.item))
        .execution_options(populate_existing=True)
    )
    specials = (
        Special(
            code=row.code,
            name=row.name_i18n,
            description=row.description_i18n,
            item_code=row.item.code,
            item_name=row.item.name_i18n,
            base_price_cents=row.item.base_price_cents,
            discount_cents=row.discount_cents,
            weekdays=frozenset(row.weekdays),
        )
        for row in (await db.execute(statement)).scalars()
    )
    return tuple(special for special in specials if special.runs_on(on))


async def todays_specials(
    db: AsyncSession,
    on: date,
    language: Language,
    *,
    cache: SpecialsCache | None = None,
) -> list[SpecialView]:
    """What to tell a customer who asks about the day's specials.

    The list of specials changes rarely and is asked for all day, so with a
    cache it is read from memory. Stock changes with every order, so it is
    read from the database every time and laid over the list.
    """
    # `is not None`, never truthiness: an empty cache has a length of zero.
    specials = cache.get(on) if cache is not None else None
    if specials is None:
        specials = await load_specials(db, on)
        if cache is not None:
            cache.set(on, specials)
    if not specials:
        return []

    sold_out = await _sold_out_item_codes(db)
    return [
        SpecialView(
            code=special.code,
            name=localize(special.name, language),
            description=localize(special.description, language)
            if special.description
            else None,
            item_code=special.item_code,
            item_name=localize(special.item_name, language),
            regular_price_cents=special.base_price_cents,
            special_price_cents=special.special_price_cents,
            saves_cents=special.saves_cents,
            in_stock=special.item_code not in sold_out,
        )
        for special in specials
    ]


async def set_special_active(db: AsyncSession, code: str, is_active: bool) -> None:
    """End a special early, or bring it back. Raises UnknownSpecial.

    The caller commits, and then invalidates any cache of specials: doing it
    in that order means a reader can never refill the cache with what was
    there before the change.
    """
    result = await db.execute(
        update(models.Special)
        .where(models.Special.code == code)
        .values(is_active=is_active, updated_at=func.now())
        .returning(models.Special.id)
    )
    if result.scalar_one_or_none() is None:
        raise UnknownSpecial(code)


async def suggest_combo(
    db: AsyncSession,
    customer_session: CustomerSession,
    units_by_category: Mapping[str, int],
) -> SuggestionView | None:
    """A combo this cart has started and could complete, if one may be offered.

    A combo is offered during one customer turn only. The first time it comes
    up it is recorded as open, and it keeps coming back, kept up to date,
    until `close_suggestions` is called when the customer next speaks. After
    that it is never offered to this session again, whatever they answered.
    """
    rules = await load_combo_rules(db)
    if not rules:
        return None
    closed = set(
        (
            await db.execute(
                select(SessionSuggestion.code).where(
                    SessionSuggestion.session_id == customer_session.id,
                    SessionSuggestion.kind == DiscountKind.COMBO,
                    SessionSuggestion.status == SuggestionStatus.CLOSED,
                )
            )
        ).scalars()
    )
    opening = find_opening(
        apply_combos(units_by_category, rules).leftover,
        rules,
        skip=closed,
        sellable=await _sellable_categories(db),
    )
    if opening is None:
        return None

    await db.execute(
        insert(SessionSuggestion)
        .values(
            session_id=customer_session.id,
            kind=DiscountKind.COMBO,
            code=opening.rule.code,
        )
        .on_conflict_do_nothing()
    )
    language = customer_session.language or DEFAULT_LANGUAGE
    return SuggestionView(
        kind=DiscountKind.COMBO,
        code=opening.rule.code,
        name=localize(opening.rule.name, language),
        missing=_category_refs(opening.missing, language),
        saves_cents=opening.rule.discount_cents,
    )


async def close_suggestions(db: AsyncSession, session_id: uuid.UUID) -> None:
    """The customer has spoken: whatever was on offer has had its one chance."""
    await db.execute(
        update(SessionSuggestion)
        .where(
            SessionSuggestion.session_id == session_id,
            SessionSuggestion.status == SuggestionStatus.OPEN,
        )
        .values(status=SuggestionStatus.CLOSED, updated_at=func.now())
    )


def _category_refs(
    slots: tuple[ComboSlot, ...], language: Language
) -> list[CategoryRef]:
    return [
        CategoryRef(
            category_code=slot.category_code,
            category_name=localize(slot.category_name, language),
        )
        for slot in slots
    ]


async def _sold_out_item_codes(db: AsyncSession) -> set[str]:
    sold_out = await sold_out_item_ids(db)
    if not sold_out:
        return set()
    codes = await db.execute(select(MenuItem.code).where(MenuItem.id.in_(sold_out)))
    return set(codes.scalars())


async def _sellable_categories(db: AsyncSession) -> set[str]:
    """Categories with at least one item that can be bought right now."""
    rows = await db.execute(
        select(MenuCategory.code, MenuItem.id)
        .join(MenuItem, MenuItem.category_id == MenuCategory.id)
        .where(MenuItem.is_active)
    )
    sold_out = await sold_out_item_ids(db)
    return {code for code, item_id in rows if item_id not in sold_out}
