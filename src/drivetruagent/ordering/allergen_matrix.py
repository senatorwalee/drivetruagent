"""Reads the allergen matrix: for one item as configured, or for every line
in a cart, with the choices on each line."""

import uuid
from collections.abc import Collection
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import AllergenDeclaration, MenuCategory, MenuItem
from drivetruagent.enums import Allergen, AllergenLevel, Language
from drivetruagent.ordering.allergens import Assessment, Declaration, assess
from drivetruagent.ordering.cart import get_cart
from drivetruagent.ordering.catalog import load_item_by_code
from drivetruagent.ordering.errors import UnknownItem, UnknownOption, UnknownOptionGroup
from drivetruagent.ordering.inventory import sold_out_item_ids
from drivetruagent.ordering.selection import ItemSpec, OptionSpec, Selections
from drivetruagent.ordering.views import localize


class ContainsView(BaseModel):
    allergen: Allergen
    # What it comes from: the item's own name, or the options that add it.
    sources: list[str]


class MayContainView(BaseModel):
    allergen: Allergen
    because: list[str]


class OptionAddsView(BaseModel):
    group_code: str
    option_code: str
    option_name: str
    contains: list[Allergen]
    may_contain: list[Allergen]


class AllergenReport(BaseModel):
    item_code: str
    item_name: str
    # The cart line this is about, when the cart was checked.
    line_no: int | None = None
    # False: the item's allergens were never reviewed. Nothing below may be
    # relied on, and the lists are empty.
    verified: bool
    reviewed_on: date | None
    # The choices this report took into account.
    options: list[str]
    contains: list[ContainsView]
    may_contain: list[MayContainView]
    # Asked about, and declared neither way. Not the same as "free of".
    not_listed: list[Allergen]
    options_that_add: list[OptionAddsView]


async def item_allergens(
    db: AsyncSession,
    item_code: str,
    selections: Selections | None,
    asked: list[Allergen] | None,
    language: Language,
    *,
    line_no: int | None = None,
) -> AllergenReport:
    """One item with these choices. A choice not given counts as its default.

    Raises UnknownItem, UnknownOptionGroup or UnknownOption.
    """
    row = (
        await db.execute(
            select(MenuItem.id, MenuItem.allergens_reviewed_on).where(
                MenuItem.code == item_code
            )
        )
    ).one_or_none()
    item = await load_item_by_code(db, item_code)
    if row is None or item is None:
        raise UnknownItem(item_code)
    item_id, reviewed_on = row

    chosen = _chosen_options(item, selections or {})
    declarations = [] if reviewed_on is None else await _declarations(db, item_id)
    assessment = assess(declarations, {o.id for o in chosen}, asked)
    return _report(item, chosen, assessment, reviewed_on, language, line_no)


class ItemWithout(BaseModel):
    item_code: str
    item_name: str


async def items_without(
    db: AsyncSession,
    allergens: Collection[Allergen],
    language: Language,
    *,
    category_code: str | None = None,
    exclude: Collection[str] = (),
    limit: int = 3,
) -> list[ItemWithout]:
    """In-stock items whose reviewed allergen information lists none of
    `allergens`, as contained or as "may contain", with their default
    choices. From one category when given, in menu order.

    What to offer someone avoiding an allergen. Items never reviewed are left
    out: nothing may be said about them.
    """
    statement = (
        select(MenuItem.code)
        .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
        .where(MenuItem.is_active, MenuItem.allergens_reviewed_on.is_not(None))
        .order_by(MenuCategory.sort_order, MenuItem.sort_order)
    )
    if category_code is not None:
        statement = statement.where(MenuCategory.code == category_code)
    sold_out = await sold_out_item_ids(db)
    rows = (await db.execute(statement.add_columns(MenuItem.id))).all()
    found: list[ItemWithout] = []
    for code, item_id in rows:
        if len(found) >= limit:
            break
        if code in exclude or item_id in sold_out:
            continue
        report = await item_allergens(db, code, None, list(allergens), language)
        if report.verified and not report.contains and not report.may_contain:
            found.append(ItemWithout(item_code=code, item_name=report.item_name))
    return found


async def cart_allergens(
    db: AsyncSession,
    session_id: uuid.UUID,
    asked: list[Allergen] | None,
    language: Language,
    *,
    tax_rate: Decimal,
) -> list[AllergenReport]:
    """Every line in the session's cart, with the choices on that line."""
    cart = await get_cart(db, session_id, tax_rate=tax_rate)
    reports: list[AllergenReport] = []
    for line in cart.lines:
        selections: dict[str, list[str]] = {}
        for option in line.options:
            selections.setdefault(option.group_code, []).append(option.option_code)
        reports.append(
            await item_allergens(
                db, line.item_code, selections, asked, language, line_no=line.line_no
            )
        )
    return reports


def _chosen_options(item: ItemSpec, selections: Selections) -> list[OptionSpec]:
    unknown = set(selections) - {group.code for group in item.groups}
    if unknown:
        raise UnknownOptionGroup(
            item.code, sorted(unknown)[0], [group.code for group in item.groups]
        )
    chosen: list[OptionSpec] = []
    for group in item.groups:
        codes = selections.get(group.code)
        if codes is None:
            if group.default_option is not None:
                chosen.append(group.default_option)
            continue
        for code in codes:
            option = group.option(code)
            if option is None:
                raise UnknownOption(group.code, code, [o.code for o in group.options])
            chosen.append(option)
    return chosen


async def _declarations(db: AsyncSession, item_id: uuid.UUID) -> list[Declaration]:
    rows = (
        await db.execute(
            select(AllergenDeclaration).where(AllergenDeclaration.item_id == item_id)
        )
    ).scalars()
    return [
        Declaration(row.allergen, row.level, row.option_id, row.note_i18n)
        for row in rows
    ]


def _report(
    item: ItemSpec,
    chosen: list[OptionSpec],
    assessment: Assessment,
    reviewed_on: date | None,
    language: Language,
    line_no: int | None,
) -> AllergenReport:
    item_name = localize(item.name, language)
    options = {
        option.id: (group, option) for group in item.groups for option in group.options
    }

    def source_name(option_id: uuid.UUID | None) -> str:
        return (
            item_name
            if option_id is None
            else localize(options[option_id][1].name, language)
        )

    return AllergenReport(
        item_code=item.code,
        item_name=item_name,
        line_no=line_no,
        verified=reviewed_on is not None,
        reviewed_on=reviewed_on,
        options=[localize(option.name, language) for option in chosen],
        contains=[
            ContainsView(
                allergen=found.allergen,
                sources=[source_name(source) for source in found.sources],
            )
            for found in assessment.contains
        ],
        may_contain=[
            MayContainView(
                allergen=found.allergen,
                because=[localize(note, language) for note in found.notes],
            )
            for found in assessment.may_contain
        ],
        not_listed=list(assessment.not_listed),
        options_that_add=[
            OptionAddsView(
                group_code=options[adds.option_id][0].code,
                option_code=options[adds.option_id][1].code,
                option_name=localize(options[adds.option_id][1].name, language),
                contains=[
                    d.allergen for d in adds.adds if d.level is AllergenLevel.CONTAINS
                ],
                may_contain=[
                    d.allergen
                    for d in adds.adds
                    if d.level is AllergenLevel.MAY_CONTAIN
                ],
            )
            for adds in assessment.options_that_add
        ],
    )
