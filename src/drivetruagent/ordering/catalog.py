"""Reads menu items from the database into the plain values the rules use."""

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import MenuItem, OptionGroup
from drivetruagent.ordering.selection import GroupSpec, ItemSpec, OptionSpec

# Load an item together with its category, its groups and their options in a
# few queries, instead of one query per group. Relationships are lazy="raise",
# so anything not loaded here would fail loudly rather than quietly query again.
# populate_existing makes each load reflect the database as it is now, even if
# the same rows were read earlier in this transaction.
_WITH_DETAILS = (
    selectinload(MenuItem.category),
    selectinload(MenuItem.option_groups).selectinload(OptionGroup.options),
)


async def load_item_by_code(db: AsyncSession, item_code: str) -> ItemSpec | None:
    statement = (
        select(MenuItem)
        .where(MenuItem.code == item_code)
        .options(*_WITH_DETAILS)
        .execution_options(populate_existing=True)
    )
    item = (await db.execute(statement)).scalar_one_or_none()
    return to_item_spec(item) if item else None


async def load_items_by_id(
    db: AsyncSession, item_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ItemSpec]:
    if not item_ids:
        return {}
    statement = (
        select(MenuItem)
        .where(MenuItem.id.in_(item_ids))
        .options(*_WITH_DETAILS)
        .execution_options(populate_existing=True)
    )
    items = (await db.execute(statement)).scalars()
    return {item.id: to_item_spec(item) for item in items}


def to_item_spec(item: MenuItem) -> ItemSpec:
    """Needs the item's category, groups and options to be loaded already."""
    return ItemSpec(
        id=item.id,
        code=item.code,
        name=item.name_i18n,
        category_code=item.category.code,
        base_price_cents=item.base_price_cents,
        is_active=item.is_active,
        groups=tuple(
            GroupSpec(
                code=group.code,
                name=group.name_i18n,
                min_select=group.min_select,
                max_select=group.max_select,
                options=tuple(
                    OptionSpec(
                        id=option.id,
                        code=option.code,
                        name=option.name_i18n,
                        price_delta_cents=option.price_delta_cents,
                        is_default=option.is_default,
                        is_active=option.is_active,
                    )
                    for option in group.options
                ),
            )
            for group in item.option_groups
        ),
    )
