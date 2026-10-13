"""Load the menu from a data file into the database.

    uv run python -m drivetruagent.seed [path/to/menu.json]

The file is checked in full before anything is written, and the load runs in
one transaction, so a mistake in the file never leaves a half-loaded menu.
Running it again is safe: rows are matched by their codes and updated.

The database is made to match the file. Items and options that are no longer
in the file are deactivated, not deleted, because past orders refer to them.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Table, delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.config import get_settings
from drivetruagent.db.base import Base
from drivetruagent.db.models import (
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    Option,
    OptionGroup,
)
from drivetruagent.db.session import create_engine, create_session_factory
from drivetruagent.enums import Language
from drivetruagent.ordering.inventory import set_on_hand

DEFAULT_MENU_PATH = Path("data/menu.json")

Code = Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
NonEmptyText = Annotated[str, Field(min_length=1)]


class SeedModel(BaseModel):
    # extra="forbid" turns a misspelt field name into an error, not a silent skip.
    model_config = ConfigDict(extra="forbid")


class LocalizedTextSeed(SeedModel):
    """A name or description. Every language the app supports is required."""

    en: NonEmptyText
    fr: NonEmptyText


class OptionSeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    price_delta_cents: int = Field(default=0, ge=0)
    default: bool = False


class OptionGroupSeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    min_select: int = Field(ge=0)
    max_select: int = Field(ge=1)
    options: list[OptionSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def check_group_is_consistent(self) -> Self:
        if self.max_select < self.min_select:
            raise ValueError("max_select must be at least min_select")
        if len(self.options) < self.min_select:
            raise ValueError("fewer options than min_select requires")
        if len(self.options) < self.max_select:
            raise ValueError("max_select is larger than the number of options")
        _require_unique([option.code for option in self.options], "option code")

        defaults = [option for option in self.options if option.default]
        if len(defaults) > 1:
            raise ValueError("a group can have at most one default option")
        if defaults and (self.min_select, self.max_select) != (1, 1):
            # A default only has meaning for "pick exactly one" groups.
            raise ValueError("a default option needs min_select = max_select = 1")
        return self


class MenuItemSeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    description: LocalizedTextSeed | None = None
    base_price_cents: int = Field(ge=0)
    # Units on hand. Leave it out for an item whose stock is not tracked.
    stock: int | None = Field(default=None, ge=0)
    aliases: dict[Language, list[str]] = Field(default_factory=dict)
    option_groups: list[OptionGroupSeed] = Field(default_factory=list)

    @field_validator("aliases")
    @classmethod
    def normalise_aliases(
        cls, aliases: dict[Language, list[str]]
    ) -> dict[Language, list[str]]:
        normalised: dict[Language, list[str]] = {}
        for language, values in aliases.items():
            cleaned = [value.strip().lower() for value in values]
            if any(not value for value in cleaned):
                raise ValueError("an alias cannot be empty")
            _require_unique(cleaned, f"{language.value} alias")
            normalised[language] = cleaned
        return normalised

    @model_validator(mode="after")
    def check_group_codes_are_unique(self) -> Self:
        _require_unique([group.code for group in self.option_groups], "group code")
        return self


class MenuCategorySeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    items: list[MenuItemSeed] = Field(default_factory=list)


class MenuSeed(SeedModel):
    categories: list[MenuCategorySeed] = Field(min_length=1)

    @model_validator(mode="after")
    def check_codes_are_unique(self) -> Self:
        _require_unique([category.code for category in self.categories], "category")
        _require_unique(
            [item.code for category in self.categories for item in category.items],
            "item code",
        )
        return self


class SeedSummary(BaseModel):
    categories: int
    items: int
    option_groups: int
    options: int
    aliases: int


def _require_unique(values: list[str], what: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate {what}: {', '.join(duplicates)}")


def load_menu(path: Path) -> MenuSeed:
    """Read and validate a menu file. Raises before any database work on error."""
    return MenuSeed.model_validate(json.loads(path.read_text(encoding="utf-8")))


async def _upsert(
    session: AsyncSession,
    model: type[Base],
    match_on: list[str],
    values: dict[str, Any],
) -> uuid.UUID:
    """Insert a row, or update the existing row with the same `match_on` values."""
    table = cast(Table, model.__table__)
    changes = {key: value for key, value in values.items() if key not in match_on}
    statement = (
        insert(table)
        .values(**values)
        .on_conflict_do_update(
            index_elements=match_on,
            # This bypasses the ORM, so updated_at has to be set by hand.
            set_={**changes, "updated_at": func.now()},
        )
        .returning(table.c.id)
    )
    return (await session.execute(statement)).scalar_one()


async def seed_menu(session: AsyncSession, menu: MenuSeed) -> SeedSummary:
    """Make the catalog tables match `menu`. The caller owns the transaction."""
    item_ids: list[uuid.UUID] = []
    group_count = option_count = alias_count = 0

    for category_position, category in enumerate(menu.categories):
        category_id = await _upsert(
            session,
            MenuCategory,
            ["code"],
            {
                "code": category.code,
                "name_i18n": category.name.model_dump(),
                "sort_order": category_position,
            },
        )

        for item_position, item in enumerate(category.items):
            item_id = await _upsert(
                session,
                MenuItem,
                ["code"],
                {
                    "code": item.code,
                    "category_id": category_id,
                    "name_i18n": item.name.model_dump(),
                    "description_i18n": (
                        item.description.model_dump() if item.description else None
                    ),
                    "base_price_cents": item.base_price_cents,
                    "is_active": True,
                    "sort_order": item_position,
                },
            )
            item_ids.append(item_id)

            group_ids = [
                await _seed_group(session, item_id, group_position, group)
                for group_position, group in enumerate(item.option_groups)
            ]
            group_count += len(group_ids)
            option_count += sum(len(group.options) for group in item.option_groups)

            # Groups dropped from the file are removed, along with their options.
            await session.execute(
                delete(OptionGroup).where(
                    OptionGroup.item_id == item_id, OptionGroup.id.not_in(group_ids)
                )
            )
            alias_count += await _replace_aliases(session, item_id, item.aliases)
            if item.stock is not None:
                # Sets the count to the file's value, which also restocks.
                await set_on_hand(session, item.code, item.stock)

    await session.execute(
        update(MenuItem)
        .where(MenuItem.id.not_in(item_ids), MenuItem.is_active)
        .values(is_active=False, updated_at=func.now())
    )

    return SeedSummary(
        categories=len(menu.categories),
        items=len(item_ids),
        option_groups=group_count,
        options=option_count,
        aliases=alias_count,
    )


async def _seed_group(
    session: AsyncSession, item_id: uuid.UUID, position: int, group: OptionGroupSeed
) -> uuid.UUID:
    group_id = await _upsert(
        session,
        OptionGroup,
        ["item_id", "code"],
        {
            "item_id": item_id,
            "code": group.code,
            "name_i18n": group.name.model_dump(),
            "min_select": group.min_select,
            "max_select": group.max_select,
            "sort_order": position,
        },
    )

    # Clear the old default first. Only one default per group is allowed, so
    # moving it to another option would otherwise collide halfway through.
    await session.execute(
        update(Option)
        .where(Option.option_group_id == group_id, Option.is_default)
        .values(is_default=False)
    )

    option_ids = [
        await _upsert(
            session,
            Option,
            ["option_group_id", "code"],
            {
                "option_group_id": group_id,
                "code": option.code,
                "name_i18n": option.name.model_dump(),
                "price_delta_cents": option.price_delta_cents,
                "is_default": option.default,
                "is_active": True,
                "sort_order": option_position,
            },
        )
        for option_position, option in enumerate(group.options)
    ]

    await session.execute(
        update(Option)
        .where(
            Option.option_group_id == group_id,
            Option.id.not_in(option_ids),
            Option.is_active,
        )
        .values(is_active=False, updated_at=func.now())
    )
    return group_id


async def _replace_aliases(
    session: AsyncSession, item_id: uuid.UUID, aliases: dict[Language, list[str]]
) -> int:
    rows = [
        {"item_id": item_id, "language": language, "alias": alias}
        for language, values in aliases.items()
        for alias in values
    ]
    await session.execute(delete(MenuItemAlias).where(MenuItemAlias.item_id == item_id))
    if rows:
        await session.execute(insert(MenuItemAlias), rows)
    return len(rows)


async def run(path: Path) -> SeedSummary:
    """Validate the file, then load it in a single transaction."""
    menu = load_menu(path)
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            return await seed_menu(session, menu)
    finally:
        await engine.dispose()


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MENU_PATH
    summary = asyncio.run(run(path))
    print(f"Seeded menu from {path}: {summary.model_dump()}")


if __name__ == "__main__":
    main()
