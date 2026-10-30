"""Reading the menu: search, one item's choices, and the whole menu.

These are the lookups the agent uses to turn what a customer said into item
and option codes. They only read; they never change anything.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import MenuCategory, MenuItem, OptionGroup
from drivetruagent.enums import Language
from drivetruagent.ordering.catalog import to_item_spec
from drivetruagent.ordering.errors import UnknownItem
from drivetruagent.ordering.inventory import sold_out_item_ids
from drivetruagent.ordering.search import (
    Candidate,
    contains_phrase,
    normalise,
    not_offered,
    search,
)
from drivetruagent.ordering.selection import GroupSpec, ItemSpec
from drivetruagent.ordering.views import (
    CategoryView,
    ChoiceGroupView,
    ChoiceOptionView,
    ItemDetails,
    MenuItemSummary,
    MenuMatch,
    MenuView,
    OfferedElsewhere,
    localize,
)

DEFAULT_SEARCH_LIMIT = 5


@dataclass(frozen=True)
class CatalogEntry:
    """A menu item with what is needed to find it and describe it."""

    item: ItemSpec
    category_code: str
    category_name: dict[str, str]
    description: dict[str, str] | None
    aliases: tuple[str, ...]


async def search_menu(
    db: AsyncSession,
    query: str,
    language: Language,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[MenuMatch]:
    """Items that match what the customer said, best first.

    Names and aliases in every language are searched, because customers mix
    them ("un cheeseburger"). `language` only decides which name is returned.
    A vague word such as "burger" returns several items with equal scores;
    the agent should then ask which one.
    """
    entries = await _load_active_entries(db)
    sold_out = await sold_out_item_ids(db)
    candidates = [Candidate(value=entry, phrases=_phrases(entry)) for entry in entries]
    query_words = normalise(query)
    # Other items named in the same breath ("a medium fries and a
    # cheeseburger"): their words, and their choices ("medium"), are not
    # something missing from the item matched.
    also_named: set[str] = set()
    for entry in entries:
        for phrase in _phrases(entry):
            words = normalise(phrase)
            if contains_phrase(query_words, words):
                also_named.update(words)
                also_named.update(_choice_words(entry.item))
    results: list[MenuMatch] = []
    for found in search(query, candidates, limit):
        entry = found.value
        missing = not_offered(found.leftover, _choice_words(entry.item) | also_named)
        results.append(
            MenuMatch(
                **_summary(entry.item, language, sold_out).model_dump(),
                category_code=entry.category_code,
                score=found.score,
                match="close" if missing else "exact",
                not_offered=list(missing),
                offered_elsewhere=_offered_elsewhere(missing, entry, entries, language),
            )
        )
    return results


def _phrases(entry: CatalogEntry) -> tuple[str, ...]:
    """Every phrase an item is known by: its names, aliases and code."""
    return (
        *entry.item.name.values(),
        *entry.aliases,
        entry.item.code.replace("_", " "),
    )


def _choice_words(item: ItemSpec) -> set[str]:
    """Words for the item's choices, in every language: "large", "vanilla",
    "size". Saying one of them is asking for this item, not something else."""
    words: set[str] = set()
    for group in item.groups:
        for name in group.name.values():
            words.update(normalise(name))
        for option in group.available_options:
            for name in option.name.values():
                words.update(normalise(name))
            words.update(normalise(option.code.replace("_", " ")))
    return words


def _offered_elsewhere(
    missing: tuple[str, ...],
    entry: CatalogEntry,
    entries: list[CatalogEntry],
    language: Language,
    limit: int = 3,
) -> list[OfferedElsewhere]:
    """Other items that have a missing word, as a choice or in their name:
    the "something similar" the agent can offer."""
    found: list[OfferedElsewhere] = []
    for word in missing:
        for other in entries:
            if other is entry or len(found) >= limit:
                continue
            name_words = {w for phrase in _phrases(other) for w in normalise(phrase)}
            if word in name_words or word in _choice_words(other.item):
                found.append(
                    OfferedElsewhere(
                        word=word,
                        item_code=other.item.code,
                        item_name=localize(other.item.name, language),
                    )
                )
    return found


async def get_item_details(
    db: AsyncSession, item_code: str, language: Language
) -> ItemDetails:
    """One item with every choice it offers. Raises UnknownItem."""
    statement = _entries_query().where(MenuItem.code == item_code)
    item = (await db.execute(statement)).scalar_one_or_none()
    if item is None:
        raise UnknownItem(item_code)
    entry = _to_entry(item)
    sold_out = await sold_out_item_ids(db)

    return ItemDetails(
        **_summary(entry.item, language, sold_out).model_dump(),
        category_code=entry.category_code,
        description=localize(entry.description, language)
        if entry.description
        else None,
        available=entry.item.is_active,
        choices=[
            _choice_view(group, language)
            for group in entry.item.groups
            # A group left with nothing to choose is no choice at all.
            if group.available_options
        ],
    )


async def get_menu(db: AsyncSession, language: Language) -> MenuView:
    """Every available item, grouped by category, in display order."""
    categories: dict[str, CategoryView] = {}
    sold_out = await sold_out_item_ids(db)
    for entry in await _load_active_entries(db):
        category = categories.setdefault(
            entry.category_code,
            CategoryView(
                category_code=entry.category_code,
                category_name=localize(entry.category_name, language),
                items=[],
            ),
        )
        category.items.append(_summary(entry.item, language, sold_out))
    return MenuView(categories=list(categories.values()))


async def alternatives_to(
    db: AsyncSession, item_code: str, language: Language, limit: int = 3
) -> list[MenuItemSummary]:
    """Other items from the same category that are in stock, in menu order.

    Offered to the customer when what they asked for has run out.
    """
    entries = await _load_active_entries(db)
    sold_out = await sold_out_item_ids(db)
    category = next(
        (entry.category_code for entry in entries if entry.item.code == item_code),
        None,
    )
    return [
        _summary(entry.item, language, sold_out)
        for entry in entries
        if entry.category_code == category
        and entry.item.code != item_code
        and entry.item.id not in sold_out
    ][:limit]


# --- Helpers ----------------------------------------------------------------


def must_ask(group: GroupSpec) -> bool:
    """True if the customer has to be asked: a choice is required and nothing
    sensible can be assumed."""
    return group.min_select >= 1 and group.default_option is None


def _summary(
    item: ItemSpec, language: Language, sold_out: set[uuid.UUID]
) -> MenuItemSummary:
    return MenuItemSummary(
        item_code=item.code,
        item_name=localize(item.name, language),
        base_price_cents=item.base_price_cents,
        must_ask=[group.code for group in item.groups if must_ask(group)],
        in_stock=item.id not in sold_out,
    )


def _choice_view(group: GroupSpec, language: Language) -> ChoiceGroupView:
    default = group.default_option
    return ChoiceGroupView(
        group_code=group.code,
        group_name=localize(group.name, language),
        min_select=group.min_select,
        max_select=group.max_select,
        must_ask=must_ask(group),
        default_option_code=default.code if default else None,
        options=[
            ChoiceOptionView(
                option_code=option.code,
                option_name=localize(option.name, language),
                price_delta_cents=option.price_delta_cents,
            )
            for option in group.available_options
        ],
    )


def _entries_query() -> Select[MenuItem]:
    return (
        select(MenuItem)
        .join(MenuItem.category)
        .options(
            selectinload(MenuItem.category),
            selectinload(MenuItem.aliases),
            selectinload(MenuItem.option_groups).selectinload(OptionGroup.options),
        )
        .order_by(MenuCategory.sort_order, MenuItem.sort_order)
        .execution_options(populate_existing=True)
    )


async def _load_active_entries(db: AsyncSession) -> list[CatalogEntry]:
    statement = _entries_query().where(MenuItem.is_active)
    return [_to_entry(item) for item in (await db.execute(statement)).scalars()]


def _to_entry(item: MenuItem) -> CatalogEntry:
    return CatalogEntry(
        item=to_item_spec(item),
        category_code=item.category.code,
        category_name=item.category.name_i18n,
        description=item.description_i18n,
        aliases=tuple(alias.alias for alias in item.aliases),
    )
