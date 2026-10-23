"""Changing the menu: adding items, editing them, taking them off, and photos.

For the menu page a manager uses. Choices (option groups), aliases and
allergens are not edited here; they still come from data/menu.json.

Items are never deleted, because past orders point at them. Taking one off
the menu sets `is_active` to false, and it can be put back.

Nothing here commits. The caller owns the transaction.
"""

import re
import unicodedata
import uuid
from datetime import date
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import (
    Inventory,
    MenuCategory,
    MenuItem,
    MenuItemPhoto,
    OptionGroup,
    Special,
)
from drivetruagent.enums import Language
from drivetruagent.ordering import inventory
from drivetruagent.ordering.errors import (
    DuplicateItemName,
    NoPhoto,
    PriceBelowSpecial,
    UnknownCategory,
    UnknownItem,
)
from drivetruagent.ordering.photos import CheckedPhoto, check_photo
from drivetruagent.ordering.views import localize

# Generous for a drive-through, and small enough to catch a typo of 100x.
MAX_PRICE_CENTS = 100_000
MAX_STOCK = 100_000
# Codes made from names are cut to this length, before any _2 suffix.
_CODE_LENGTH = 40

Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)]


# --- What the page sees -----------------------------------------------------


class AdminCategoryView(BaseModel):
    category_code: str
    category_name: str


class AdminChoiceOption(BaseModel):
    option_name: str
    price_delta_cents: int


class AdminChoiceGroup(BaseModel):
    """One choice the item offers. Shown, not edited, on the menu page."""

    group_name: str
    required: bool
    options: list[AdminChoiceOption]


class AdminPhotoView(BaseModel):
    # Changes whenever the photo does; put it in the photo's URL.
    version: str
    credit: str | None


class AdminItemView(BaseModel):
    item_code: str
    category_code: str
    name_en: str
    # None: the English name is used in French too.
    name_fr: str | None
    description_en: str | None
    description_fr: str | None
    base_price_cents: int
    # False: taken off the menu. Guests and the agent do not see it.
    is_active: bool
    # Units on hand; None when stock is not tracked (unlimited).
    stock: int | None
    sold_out: bool
    photo: AdminPhotoView | None
    # None: never reviewed, so the agent says nothing about its allergens.
    allergens_reviewed_on: date | None
    choices: list[AdminChoiceGroup]


class AdminMenuView(BaseModel):
    categories: list[AdminCategoryView]
    # In menu order: by category, then by position in the category.
    items: list[AdminItemView]


# --- What the page sends ----------------------------------------------------


class _ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "name_fr", "description_en", "description_fr", mode="after", check_fields=False
    )
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        """An emptied field means "none", not an empty string."""
        return value or None


class NewItem(_ItemFields):
    name_en: Annotated[Name, StringConstraints(min_length=1)]
    name_fr: Name | None = None
    description_en: Description | None = None
    description_fr: Description | None = None
    category_code: str
    base_price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)
    # Leave out, or None, to not track stock.
    stock: int | None = Field(default=None, ge=0, le=MAX_STOCK)

    @model_validator(mode="after")
    def check_french_description_has_english(self) -> Self:
        # English is the fallback for every name and description.
        if self.description_fr and not self.description_en:
            raise ValueError("a French description needs an English one")
        return self


class ItemChanges(_ItemFields):
    """Only the fields that are sent are changed. `stock: null` stops
    tracking stock; leaving `stock` out leaves it as it is."""

    name_en: Annotated[Name, StringConstraints(min_length=1)] | None = None
    name_fr: Name | None = None
    description_en: Description | None = None
    description_fr: Description | None = None
    category_code: str | None = None
    base_price_cents: int | None = Field(default=None, ge=0, le=MAX_PRICE_CENTS)
    stock: int | None = Field(default=None, ge=0, le=MAX_STOCK)
    is_active: bool | None = None

    @model_validator(mode="after")
    def check_required_fields_are_not_cleared(self) -> Self:
        for field in ("name_en", "category_code", "base_price_cents", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        return self

    @model_validator(mode="after")
    def check_french_description_has_english(self) -> Self:
        # When English is not sent, the item's current one stands in.
        english_cleared = "description_en" in self.model_fields_set and (
            not self.description_en
        )
        if self.description_fr and english_cleared:
            raise ValueError("a French description needs an English one")
        return self


# --- Reading ----------------------------------------------------------------


async def read_admin_menu(db: AsyncSession) -> AdminMenuView:
    """Every item, those taken off the menu too, with the categories."""
    categories = (
        await db.execute(select(MenuCategory).order_by(MenuCategory.sort_order))
    ).scalars()
    items = await _load_items(db)
    return AdminMenuView(
        categories=[
            AdminCategoryView(
                category_code=category.code,
                category_name=localize(category.name_i18n, Language.EN),
            )
            for category in categories
        ],
        items=await _views(db, items),
    )


async def read_item(db: AsyncSession, item_code: str) -> AdminItemView:
    items = await _load_items(db, item_code)
    if not items:
        raise UnknownItem(item_code)
    return (await _views(db, items))[0]


async def read_photo(db: AsyncSession, item_code: str) -> MenuItemPhoto:
    """The item's photo. Raises UnknownItem, or NoPhoto if it has none."""
    item_id = await _item_id(db, item_code)
    photo = await db.get(MenuItemPhoto, item_id)
    if photo is None:
        raise NoPhoto(item_code)
    return photo


# --- Changing ---------------------------------------------------------------


async def create_item(db: AsyncSession, new: NewItem) -> AdminItemView:
    """Add an item at the end of its category. Its code is made from its
    English name and never changes after."""
    category_id = await _category_id(db, new.category_code)
    await _check_name_is_free(db, new.name_en, None)
    item = MenuItem(
        code=await _free_code(db, new.name_en),
        category_id=category_id,
        name_i18n=_localized(new.name_en, new.name_fr),
        description_i18n=_description(new.description_en, new.description_fr),
        base_price_cents=new.base_price_cents,
        sort_order=await _next_position(db, category_id),
        # A new item's allergens have not been reviewed: the agent will not
        # say anything about them until someone does.
        allergens_reviewed_on=None,
    )
    db.add(item)
    await db.flush()
    if new.stock is not None:
        await inventory.set_on_hand(db, item.code, new.stock)
    return await read_item(db, item.code)


async def update_item(
    db: AsyncSession, item_code: str, changes: ItemChanges
) -> AdminItemView:
    """Apply the fields that were sent. Raises UnknownItem."""
    item = (
        await db.execute(
            select(MenuItem).where(MenuItem.code == item_code).with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise UnknownItem(item_code)
    sent = changes.model_fields_set

    if "name_en" in sent or "name_fr" in sent:
        name_en = changes.name_en if "name_en" in sent else item.name_i18n["en"]
        name_fr = changes.name_fr if "name_fr" in sent else item.name_i18n.get("fr")
        assert name_en is not None  # checked by ItemChanges
        if name_en.casefold() != item.name_i18n["en"].casefold():
            await _check_name_is_free(db, name_en, item.id)
        item.name_i18n = _localized(name_en, name_fr)

    if "description_en" in sent or "description_fr" in sent:
        old = item.description_i18n or {}
        english = changes.description_en if "description_en" in sent else old.get("en")
        french = changes.description_fr if "description_fr" in sent else old.get("fr")
        if french and not english:
            # The English one was cleared: a French one alone cannot stand.
            french = None
        item.description_i18n = _description(english, french)

    if changes.category_code is not None:
        category_id = await _category_id(db, changes.category_code)
        if category_id != item.category_id:
            item.category_id = category_id
            item.sort_order = await _next_position(db, category_id)

    if changes.base_price_cents is not None:
        await _check_price_leaves_something_to_pay(db, item, changes.base_price_cents)
        item.base_price_cents = changes.base_price_cents

    if changes.is_active is not None:
        item.is_active = changes.is_active

    if "stock" in sent:
        if changes.stock is None:
            await db.execute(delete(Inventory).where(Inventory.item_id == item.id))
        else:
            await inventory.set_on_hand(db, item.code, changes.stock)

    await db.flush()
    return await read_item(db, item.code)


async def set_photo(
    db: AsyncSession, item_code: str, data: bytes, credit: str | None = None
) -> AdminItemView:
    """Set or replace the item's photo. Raises PhotoNotAccepted or UnknownItem."""
    photo = check_photo(data)
    item_id = await _item_id(db, item_code)
    await store_photo(db, item_id, photo, credit)
    return await read_item(db, item_code)


async def store_photo(
    db: AsyncSession, item_id: uuid.UUID, photo: CheckedPhoto, credit: str | None
) -> None:
    """Write a checked photo for an item, replacing any it had."""
    values = {
        "content_type": photo.content_type,
        "data": photo.data,
        "sha256": photo.sha256,
        "credit": credit,
    }
    await db.execute(
        insert(MenuItemPhoto)
        .values(item_id=item_id, **values)
        .on_conflict_do_update(
            index_elements=["item_id"], set_={**values, "updated_at": func.now()}
        )
    )
    # The row was written past the ORM; drop any copy the session holds.
    cached = await db.get(MenuItemPhoto, item_id)
    if cached is not None:
        await db.refresh(cached)


async def remove_photo(db: AsyncSession, item_code: str) -> AdminItemView:
    """Remove the item's photo, if it has one. Raises UnknownItem."""
    item_id = await _item_id(db, item_code)
    await db.execute(delete(MenuItemPhoto).where(MenuItemPhoto.item_id == item_id))
    return await read_item(db, item_code)


# --- Helpers ----------------------------------------------------------------


def code_from_name(name: str) -> str:
    """A code made from a name: "Crème brûlée!" becomes "creme_brulee"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    code = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")
    return code[:_CODE_LENGTH].rstrip("_") or "item"


async def _free_code(db: AsyncSession, name: str) -> str:
    base = code_from_name(name)
    taken = set(
        (
            await db.execute(
                select(MenuItem.code).where(MenuItem.code.startswith(base))
            )
        ).scalars()
    )
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    return code


async def _check_name_is_free(
    db: AsyncSession, name_en: str, item_id: uuid.UUID | None
) -> None:
    """Refuse an English name another item on the menu already has. Items
    taken off the menu do not count: their names can be reused."""
    statement = select(MenuItem.code).where(
        MenuItem.is_active,
        func.lower(MenuItem.name_i18n["en"].astext) == name_en.casefold(),
    )
    if item_id is not None:
        statement = statement.where(MenuItem.id != item_id)
    other = (await db.execute(statement.limit(1))).scalar_one_or_none()
    if other is not None:
        raise DuplicateItemName(name_en, other)


async def _check_price_leaves_something_to_pay(
    db: AsyncSession, item: MenuItem, price_cents: int
) -> None:
    special = (
        await db.execute(
            select(Special)
            .where(
                Special.item_id == item.id,
                Special.is_active,
                Special.discount_cents >= price_cents,
            )
            .order_by(Special.discount_cents.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if special is not None:
        raise PriceBelowSpecial(item.code, special.code, special.discount_cents)


async def _item_id(db: AsyncSession, item_code: str) -> uuid.UUID:
    item_id = (
        await db.execute(select(MenuItem.id).where(MenuItem.code == item_code))
    ).scalar_one_or_none()
    if item_id is None:
        raise UnknownItem(item_code)
    return item_id


async def _category_id(db: AsyncSession, category_code: str) -> uuid.UUID:
    category_id = (
        await db.execute(
            select(MenuCategory.id).where(MenuCategory.code == category_code)
        )
    ).scalar_one_or_none()
    if category_id is None:
        raise UnknownCategory(category_code)
    return category_id


async def _next_position(db: AsyncSession, category_id: uuid.UUID) -> int:
    highest = (
        await db.execute(
            select(func.max(MenuItem.sort_order)).where(
                MenuItem.category_id == category_id
            )
        )
    ).scalar_one()
    return 0 if highest is None else highest + 1


def _localized(english: str, french: str | None) -> dict[str, str]:
    return {"en": english, "fr": french} if french else {"en": english}


def _description(english: str | None, french: str | None) -> dict[str, str] | None:
    return _localized(english, french) if english else None


async def _load_items(db: AsyncSession, item_code: str | None = None) -> list[MenuItem]:
    statement = (
        select(MenuItem)
        .join(MenuItem.category)
        .options(
            selectinload(MenuItem.category),
            selectinload(MenuItem.option_groups).selectinload(OptionGroup.options),
        )
        .order_by(MenuCategory.sort_order, MenuItem.sort_order, MenuItem.code)
        .execution_options(populate_existing=True)
    )
    if item_code is not None:
        statement = statement.where(MenuItem.code == item_code)
    return list((await db.execute(statement)).scalars())


async def _views(db: AsyncSession, items: list[MenuItem]) -> list[AdminItemView]:
    ids = [item.id for item in items]
    stock_rows = await db.execute(
        select(Inventory.item_id, Inventory.on_hand).where(Inventory.item_id.in_(ids))
    )
    stock = {item_id: on_hand for item_id, on_hand in stock_rows.all()}
    # Only the hash and credit: the bytes stay in the database.
    photo_rows = await db.execute(
        select(MenuItemPhoto.item_id, MenuItemPhoto.sha256, MenuItemPhoto.credit).where(
            MenuItemPhoto.item_id.in_(ids)
        )
    )
    photos = {
        item_id: AdminPhotoView(version=sha256[:16], credit=credit)
        for item_id, sha256, credit in photo_rows.all()
    }
    sold_out = await inventory.sold_out_item_ids(db)
    return [
        AdminItemView(
            item_code=item.code,
            category_code=item.category.code,
            name_en=item.name_i18n["en"],
            name_fr=item.name_i18n.get("fr"),
            description_en=(item.description_i18n or {}).get("en"),
            description_fr=(item.description_i18n or {}).get("fr"),
            base_price_cents=item.base_price_cents,
            is_active=item.is_active,
            stock=stock.get(item.id),
            sold_out=item.id in sold_out,
            photo=photos.get(item.id),
            allergens_reviewed_on=item.allergens_reviewed_on,
            choices=[
                AdminChoiceGroup(
                    group_name=localize(group.name_i18n, Language.EN),
                    required=group.min_select >= 1,
                    options=[
                        AdminChoiceOption(
                            option_name=localize(option.name_i18n, Language.EN),
                            price_delta_cents=option.price_delta_cents,
                        )
                        for option in group.options
                        if option.is_active
                    ],
                )
                for group in item.option_groups
            ],
        )
        for item in items
    ]
