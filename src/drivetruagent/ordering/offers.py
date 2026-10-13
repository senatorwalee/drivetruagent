"""Combos as the database holds them, and what has been suggested to whom.

The arithmetic is in `discounts`; this module reads the rules and keeps the
record that stops a customer being offered the same thing twice.
"""

import uuid
from collections.abc import Mapping

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    apply_combos,
    find_opening,
)
from drivetruagent.ordering.inventory import sold_out_item_ids
from drivetruagent.ordering.views import (
    DEFAULT_LANGUAGE,
    CategoryRef,
    ComboView,
    SuggestionView,
    localize,
)


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


async def _sellable_categories(db: AsyncSession) -> set[str]:
    """Categories with at least one item that can be bought right now."""
    rows = await db.execute(
        select(MenuCategory.code, MenuItem.id)
        .join(MenuItem, MenuItem.category_id == MenuCategory.id)
        .where(MenuItem.is_active)
    )
    sold_out = await sold_out_item_ids(db)
    return {code for code, item_id in rows if item_id not in sold_out}
