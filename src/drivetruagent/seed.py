"""Load the menu from a data file into the database.

    uv run python -m drivetruagent.seed [path/to/menu.json]

The file is checked in full before anything is written, and the load runs in
one transaction, so a mistake in the file never leaves a half-loaded menu.
Running it again is safe: rows are matched by their codes and updated.

The database is made to match the file. Items, options and combos that are no
longer in the file are deactivated, not deleted, because past orders refer to
them. That includes undoing changes made on the menu page: seeding is a reset.

    uv run python -m drivetruagent.seed --if-empty

loads the menu only into a database that has none, and otherwise leaves it
alone. The app's container starts that way, so edits survive a restart.

Photos are read from `photos/` next to the menu file.
"""

import argparse
import asyncio
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Table, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.config import get_settings
from drivetruagent.db.base import Base
from drivetruagent.db.models import (
    AllergenDeclaration,
    CartLineOption,
    ComboRule,
    ComboRuleSlot,
    DietSuitability,
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    MenuItemPhoto,
    Option,
    OptionGroup,
    RestaurantProfile,
    Special,
)
from drivetruagent.db.session import create_engine, create_session_factory
from drivetruagent.enums import Allergen, AllergenLevel, Diet, DietStatus, Language
from drivetruagent.ordering.inventory import set_on_hand
from drivetruagent.ordering.menu_admin import store_photo
from drivetruagent.ordering.photos import check_photo

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


class MayContainSeed(SeedModel):
    """Allergens that are not ingredients but may be present, and why."""

    allergens: list[Allergen] = Field(min_length=1)
    because: LocalizedTextSeed


class AllergensSeed(SeedModel):
    """What an option adds to its item's allergens."""

    contains: list[Allergen] = Field(default_factory=list)
    may_contain: list[MayContainSeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_each_allergen_is_listed_once(self) -> Self:
        listed = [*self.contains]
        for group in self.may_contain:
            listed.extend(group.allergens)
        _require_unique([allergen.value for allergen in listed], "allergen")
        return self

    def declarations(
        self,
    ) -> list[tuple[Allergen, AllergenLevel, LocalizedTextSeed | None]]:
        rows: list[tuple[Allergen, AllergenLevel, LocalizedTextSeed | None]] = [
            (allergen, AllergenLevel.CONTAINS, None) for allergen in self.contains
        ]
        for group in self.may_contain:
            rows.extend(
                (allergen, AllergenLevel.MAY_CONTAIN, group.because)
                for allergen in group.allergens
            )
        return rows


class ItemAllergensSeed(AllergensSeed):
    """An item's allergens, from the allergen matrix. Listing none is a
    statement too: a reviewed item with nothing declared."""

    reviewed_on: date


class PhotoSeed(SeedModel):
    # A file in the photos folder next to the menu file.
    file: Annotated[str, Field(pattern=r"^[a-z0-9_]+\.(jpg|png|webp)$")]
    # Who took it: "Jane Doe on Unsplash".
    credit: NonEmptyText | None = None


class OptionSeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    price_delta_cents: int = Field(default=0, ge=0)
    default: bool = False
    allergens: AllergensSeed | None = None


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


class DietSeed(SeedModel):
    """An item reviewed for a diet: certified, suitable but not certified, or
    not suitable, and why, in words a guest can hear."""

    status: DietStatus
    note: LocalizedTextSeed


class MenuItemSeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    description: LocalizedTextSeed | None = None
    # Leave it out for no photo: the menu page shows a placeholder.
    photo: PhotoSeed | None = None
    base_price_cents: int = Field(ge=0)
    # Units on hand. Leave it out for an item whose stock is not tracked.
    stock: int | None = Field(default=None, ge=0)
    aliases: dict[Language, list[str]] = Field(default_factory=dict)
    option_groups: list[OptionGroupSeed] = Field(default_factory=list)
    # Leave it out for an item whose allergens have not been reviewed: the
    # agent then says nothing about them and hands the question to a person.
    allergens: ItemAllergensSeed | None = None
    # Diets the item has been reviewed for. Leave a diet out and nothing is
    # claimed about the item for it, either way.
    diets: dict[Diet, DietSeed] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def check_option_allergens_belong_to_a_reviewed_item(self) -> Self:
        has_option_allergens = any(
            option.allergens is not None
            for group in self.option_groups
            for option in group.options
        )
        if has_option_allergens and self.allergens is None:
            raise ValueError(
                "options declare allergens but the item has no reviewed allergens"
            )
        return self


class MenuCategorySeed(SeedModel):
    code: Code
    name: LocalizedTextSeed
    items: list[MenuItemSeed] = Field(default_factory=list)


class ComboSeed(SeedModel):
    """One item from each slot's category, bought together, costs less."""

    code: Code
    name: LocalizedTextSeed
    discount_cents: int = Field(gt=0)
    # Category codes. The first is the main item the combo is built around.
    slots: list[Code] = Field(min_length=2)


class SpecialSeed(SeedModel):
    """An item that is cheaper on certain days of the week."""

    code: Code
    # The code of the item it applies to.
    item: Code
    name: LocalizedTextSeed
    description: LocalizedTextSeed | None = None
    # Taken off each unit of the item.
    discount_cents: int = Field(gt=0)
    # ISO weekdays, 1 for Monday to 7 for Sunday. Leave empty for every day.
    weekdays: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=list)

    @field_validator("weekdays")
    @classmethod
    def check_weekdays(cls, weekdays: list[int]) -> list[int]:
        if len(set(weekdays)) != len(weekdays):
            raise ValueError("a weekday is listed twice")
        return sorted(weekdays)


class MenuSeed(SeedModel):
    categories: list[MenuCategorySeed] = Field(min_length=1)
    # In priority order: when units could count toward two combos, the
    # earlier one gets them.
    combos: list[ComboSeed] = Field(default_factory=list)
    # In priority order: if two specials name one item on the same day, the
    # earlier one applies.
    specials: list[SpecialSeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_codes_are_unique(self) -> Self:
        _require_unique([category.code for category in self.categories], "category")
        _require_unique(
            [item.code for category in self.categories for item in category.items],
            "item code",
        )
        _require_unique([combo.code for combo in self.combos], "combo code")
        _require_unique([special.code for special in self.specials], "special code")
        return self

    @model_validator(mode="after")
    def check_specials_leave_something_to_pay(self) -> Self:
        prices = {
            item.code: item.base_price_cents
            for category in self.categories
            for item in category.items
        }
        for special in self.specials:
            if special.item not in prices:
                raise ValueError(
                    f"special {special.code}: there is no item {special.item}"
                )
            if special.discount_cents >= prices[special.item]:
                raise ValueError(
                    f"special {special.code}: a discount of {special.discount_cents} "
                    f"is not less than the price of {special.item} "
                    f"({prices[special.item]})"
                )
        return self

    @model_validator(mode="after")
    def check_combos_can_be_made(self) -> Self:
        cheapest = {
            category.code: min(item.base_price_cents for item in category.items)
            for category in self.categories
            if category.items
        }
        for combo in self.combos:
            unknown = sorted(set(combo.slots) - set(cheapest))
            if unknown:
                raise ValueError(
                    f"combo {combo.code}: no items in category {', '.join(unknown)}"
                )
            # The cheapest set a customer could build. A discount as large as
            # that would make food free.
            cheapest_set = sum(cheapest[slot] for slot in combo.slots)
            if combo.discount_cents >= cheapest_set:
                raise ValueError(
                    f"combo {combo.code}: a discount of {combo.discount_cents} "
                    f"is not less than its cheapest set ({cheapest_set})"
                )
        return self


class SeedSummary(BaseModel):
    categories: int
    items: int
    option_groups: int
    options: int
    aliases: int
    combos: int
    specials: int
    allergen_declarations: int
    diet_entries: int = 0


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


async def seed_menu(
    session: AsyncSession, menu: MenuSeed, photos_dir: Path | None = None
) -> SeedSummary:
    """Make the catalog tables match `menu`. The caller owns the transaction.

    With `photos_dir`, items' photos are made to match too. Without it they
    are left as they are, which keeps tests from loading 20 images each.
    """
    item_ids: list[uuid.UUID] = []
    item_ids_by_code: dict[str, uuid.UUID] = {}
    category_ids: dict[str, uuid.UUID] = {}
    group_count = option_count = alias_count = allergen_count = diet_count = 0

    for category_position, category in enumerate(menu.categories):
        category_id = category_ids[category.code] = await _upsert(
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
                    "allergens_reviewed_on": (
                        item.allergens.reviewed_on if item.allergens else None
                    ),
                },
            )
            item_ids.append(item_id)
            item_ids_by_code[item.code] = item_id

            seeded_groups = [
                await _seed_group(session, item_id, group_position, group)
                for group_position, group in enumerate(item.option_groups)
            ]
            group_ids = [group_id for group_id, _ in seeded_groups]
            group_count += len(group_ids)
            option_count += sum(len(group.options) for group in item.option_groups)

            await _drop_groups(session, item_id, group_ids)
            alias_count += await _replace_aliases(session, item_id, item.aliases)
            allergen_count += await _replace_allergens(
                session,
                item_id,
                item,
                [option_ids for _, option_ids in seeded_groups],
            )
            diet_count += await _replace_diets(session, item_id, item)
            if item.stock is not None:
                # Sets the count to the file's value, which also restocks.
                await set_on_hand(session, item.code, item.stock)
            if photos_dir is not None:
                await _seed_photo(session, item_id, item.photo, photos_dir)

    await session.execute(
        update(MenuItem)
        .where(MenuItem.id.not_in(item_ids), MenuItem.is_active)
        .values(is_active=False, updated_at=func.now())
    )
    await _seed_combos(session, menu.combos, category_ids)
    await _seed_specials(session, menu.specials, item_ids_by_code)
    # Seeding is a reset: back to the name in the settings.
    await session.execute(delete(RestaurantProfile))

    return SeedSummary(
        categories=len(menu.categories),
        items=len(item_ids),
        option_groups=group_count,
        options=option_count,
        aliases=alias_count,
        combos=len(menu.combos),
        specials=len(menu.specials),
        allergen_declarations=allergen_count,
        diet_entries=diet_count,
    )


async def _seed_specials(
    session: AsyncSession, specials: list[SpecialSeed], item_ids: dict[str, uuid.UUID]
) -> None:
    special_ids = [
        await _upsert(
            session,
            Special,
            ["code"],
            {
                "code": special.code,
                "item_id": item_ids[special.item],
                "name_i18n": special.name.model_dump(),
                "description_i18n": (
                    special.description.model_dump() if special.description else None
                ),
                "discount_cents": special.discount_cents,
                "weekdays": special.weekdays,
                "is_active": True,
                "sort_order": position,
            },
        )
        for position, special in enumerate(specials)
    ]
    await session.execute(
        update(Special)
        .where(Special.id.not_in(special_ids), Special.is_active)
        .values(is_active=False, updated_at=func.now())
    )


async def _seed_combos(
    session: AsyncSession, combos: list[ComboSeed], category_ids: dict[str, uuid.UUID]
) -> None:
    combo_ids: list[uuid.UUID] = []
    for position, combo in enumerate(combos):
        combo_id = await _upsert(
            session,
            ComboRule,
            ["code"],
            {
                "code": combo.code,
                "name_i18n": combo.name.model_dump(),
                "discount_cents": combo.discount_cents,
                "is_active": True,
                "sort_order": position,
            },
        )
        combo_ids.append(combo_id)
        # Slots carry no history of their own, so they are simply replaced.
        await session.execute(
            delete(ComboRuleSlot).where(ComboRuleSlot.combo_rule_id == combo_id)
        )
        await session.execute(
            insert(ComboRuleSlot),
            [
                {
                    "combo_rule_id": combo_id,
                    "position": slot_position,
                    "category_id": category_ids[slot],
                }
                for slot_position, slot in enumerate(combo.slots)
            ],
        )

    await session.execute(
        update(ComboRule)
        .where(ComboRule.id.not_in(combo_ids), ComboRule.is_active)
        .values(is_active=False, updated_at=func.now())
    )


async def _seed_group(
    session: AsyncSession, item_id: uuid.UUID, position: int, group: OptionGroupSeed
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Upsert one group and its options. Returns the group's id and its
    options' ids, in the file's order."""
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
    return group_id, option_ids


async def _drop_groups(
    session: AsyncSession, item_id: uuid.UUID, kept: list[uuid.UUID]
) -> None:
    """Groups no longer in the file are removed, along with their options. A
    group a cart points at can't be: it stays, with nothing left to choose."""
    dropped = select(OptionGroup.id).where(
        OptionGroup.item_id == item_id, OptionGroup.id.not_in(kept)
    )
    in_a_cart = (
        select(Option.option_group_id)
        .join(CartLineOption, CartLineOption.option_id == Option.id)
        .where(Option.option_group_id.in_(dropped))
    )
    await session.execute(
        delete(OptionGroup).where(
            OptionGroup.id.in_(dropped), OptionGroup.id.not_in(in_a_cart)
        )
    )
    await session.execute(
        update(OptionGroup).where(OptionGroup.id.in_(dropped)).values(min_select=0)
    )
    await session.execute(
        update(Option)
        .where(Option.option_group_id.in_(dropped))
        .values(is_active=False, is_default=False, updated_at=func.now())
    )


async def _replace_diets(
    session: AsyncSession, item_id: uuid.UUID, item: MenuItemSeed
) -> int:
    """Make the item's diet entries match the file."""
    rows = [
        {
            "item_id": item_id,
            "diet": diet,
            "status": entry.status,
            "note_i18n": entry.note.model_dump(),
        }
        for diet, entry in item.diets.items()
    ]
    await session.execute(
        delete(DietSuitability).where(DietSuitability.item_id == item_id)
    )
    if rows:
        await session.execute(insert(DietSuitability), rows)
    return len(rows)


async def _replace_allergens(
    session: AsyncSession,
    item_id: uuid.UUID,
    item: MenuItemSeed,
    option_ids: list[list[uuid.UUID]],
) -> int:
    """Make the item's allergen declarations match the file. `option_ids`
    holds each group's option ids, in the file's order."""
    sources: list[tuple[uuid.UUID | None, AllergensSeed | None]] = [
        (None, item.allergens)
    ]
    for group, ids in zip(item.option_groups, option_ids, strict=True):
        sources.extend(
            (option_id, option.allergens)
            for option, option_id in zip(group.options, ids, strict=True)
        )
    rows = [
        {
            "item_id": item_id,
            "option_id": option_id,
            "allergen": allergen,
            "level": level,
            "note_i18n": note.model_dump() if note else None,
        }
        for option_id, allergens in sources
        if allergens is not None
        for allergen, level, note in allergens.declarations()
    ]
    await session.execute(
        delete(AllergenDeclaration).where(AllergenDeclaration.item_id == item_id)
    )
    if rows:
        await session.execute(insert(AllergenDeclaration), rows)
    return len(rows)


async def _seed_photo(
    session: AsyncSession,
    item_id: uuid.UUID,
    photo: PhotoSeed | None,
    photos_dir: Path,
) -> None:
    if photo is None:
        await session.execute(
            delete(MenuItemPhoto).where(MenuItemPhoto.item_id == item_id)
        )
        return
    checked = check_photo((photos_dir / photo.file).read_bytes())
    await store_photo(session, item_id, checked, photo.credit)


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


def photos_dir_for(path: Path) -> Path:
    return path.parent / "photos"


async def run(path: Path, *, if_empty: bool = False) -> SeedSummary | None:
    """Validate the file, then load it in a single transaction.

    With `if_empty`, do nothing (and return None) if there is a menu already.
    """
    menu = load_menu(path)
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            has_menu = await session.scalar(select(exists().select_from(MenuItem)))
            if if_empty and has_menu:
                return None
            return await seed_menu(session, menu, photos_dir_for(path))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the menu into the database.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_MENU_PATH)
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="only if the database has no menu yet; never undo edits",
    )
    args = parser.parse_args()
    summary = asyncio.run(run(args.path, if_empty=args.if_empty))
    if summary is None:
        print("A menu is already loaded; left as it is. `make seed` resets it.")
    else:
        print(f"Seeded menu from {args.path}: {summary.model_dump()}")


if __name__ == "__main__":
    main()
