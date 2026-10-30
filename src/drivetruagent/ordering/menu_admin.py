"""Changing the menu: adding items, editing them, taking them off, and photos.

For the menu page a manager uses. An item's own allergens are reviewed here,
and its sizes set. Other choices (option groups), what choices add to
allergens, and aliases are not edited here; they still come from
data/menu.json.

Items are never deleted, because past orders point at them. Taking one off
the menu sets `is_active` to false, and it can be put back. A size is the same:
carts point at options, so a removed one is deactivated and can come back.

Nothing here commits. The caller owns the transaction.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
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
    AllergenDeclaration,
    ComboRule,
    Inventory,
    MenuCategory,
    MenuItem,
    MenuItemPhoto,
    Option,
    OptionGroup,
    Special,
)
from drivetruagent.enums import Allergen, AllergenLevel, Language
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
# More than a drive-through asks a guest to choose between.
MAX_SIZES = 8
# Codes made from names are cut to this length, before any _2 suffix.
_CODE_LENGTH = 40
# The group that holds an item's sizes. The menu file's use the same code and
# the same name.
_SIZE_GROUP_CODE = "size"
_SIZE_GROUP_NAME = {"en": "Size", "fr": "Format"}
_ALLERGEN_ORDER = {allergen: position for position, allergen in enumerate(Allergen)}

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


class AdminSize(BaseModel):
    name_en: str
    # What a guest pays for this size, before any other choice.
    price_cents: int
    # The size assumed when a guest doesn't say. With none, the agent asks.
    is_default: bool


class AdminMayContain(BaseModel):
    allergen: Allergen
    because: str


class AdminAllergens(BaseModel):
    """The item's own allergens, as reviewed. Both lists empty is a statement
    too: the restaurant said it has none of them."""

    contains: list[Allergen]
    may_contain: list[AdminMayContain]


class AdminOptionAllergens(BaseModel):
    """What a choice adds to the item. Shown, not edited, on the menu page."""

    option_name: str
    contains: list[Allergen]
    may_contain: list[Allergen]


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
    allergens: AdminAllergens | None
    option_allergens: list[AdminOptionAllergens]
    # Empty when the item has one price.
    sizes: list[AdminSize]
    # Every other choice the item offers.
    choices: list[AdminChoiceGroup]


class AdminMenuView(BaseModel):
    categories: list[AdminCategoryView]
    # In menu order: by category, then by position in the category.
    items: list[AdminItemView]


# --- What the page sends ----------------------------------------------------


class MayContain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allergen: Allergen
    # Why it may be there, in words a guest can hear. The agent reads it out.
    because: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]


class AllergenReview(BaseModel):
    """What the restaurant says about an item's allergens. Sending one marks
    the item reviewed, and a reviewed item with nothing declared is offered to
    guests avoiding an allergen. So it has to say something on purpose: what
    is in it, or `none_of_these`. A review that is merely empty is refused, so
    a missed tick can never read as "none"."""

    model_config = ConfigDict(extra="forbid")

    none_of_these: bool = False
    contains: list[Allergen] = Field(default_factory=list)
    may_contain: list[MayContain] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_it_says_one_thing(self) -> Self:
        listed = [*self.contains, *(entry.allergen for entry in self.may_contain)]
        if len(set(listed)) != len(listed):
            raise ValueError("an allergen is listed twice")
        if self.none_of_these and listed:
            raise ValueError("none_of_these cannot list allergens")
        if not self.none_of_these and not listed:
            raise ValueError("say what the item has, or none_of_these")
        return self


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_en: Annotated[Name, StringConstraints(min_length=1)]
    # What a guest pays for it. The cheapest size becomes the item's price and
    # the others what they add to it.
    price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)
    is_default: bool = False


def _check_sizes(sizes: list[Size]) -> list[Size]:
    codes = [code_from_name(size.name_en) for size in sizes]
    if len(set(codes)) != len(codes):
        raise ValueError("two sizes have the same name")
    if sum(size.is_default for size in sizes) > 1:
        raise ValueError("only one size can be the default")
    return sizes


# At least two: one size is no choice. Stock stays with the item, not the size.
Sizes = Annotated[
    list[Size],
    Field(min_length=2, max_length=MAX_SIZES),
    AfterValidator(_check_sizes),
]


def _cheapest(sizes: list[Size]) -> int:
    return min(size.price_cents for size in sizes)


class _ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "name_fr", "description_en", "description_fr", mode="after", check_fields=False
    )
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        """An emptied field means "none", not an empty string."""
        return value or None


class NewItemFields(_ItemFields):
    """An item being added, whichever page it comes from."""

    name_en: Annotated[Name, StringConstraints(min_length=1)]
    name_fr: Name | None = None
    description_en: Description | None = None
    description_fr: Description | None = None
    base_price_cents: int = Field(ge=0, le=MAX_PRICE_CENTS)
    # Leave out, or None, to not track stock.
    stock: int | None = Field(default=None, ge=0, le=MAX_STOCK)
    # Leave out, or None, and the item starts unreviewed: the agent will not
    # say anything about its allergens until someone does this.
    allergens: AllergenReview | None = None
    sizes: Sizes | None = None

    @model_validator(mode="after")
    def check_price_is_the_cheapest_size(self) -> Self:
        if self.sizes and self.base_price_cents != _cheapest(self.sizes):
            raise ValueError("base_price_cents must be the cheapest size")
        return self

    @model_validator(mode="after")
    def check_french_description_has_english(self) -> Self:
        # English is the fallback for every name and description.
        if self.description_fr and not self.description_en:
            raise ValueError("a French description needs an English one")
        return self


class NewItem(NewItemFields):
    category_code: str


class ItemChanges(_ItemFields):
    """Only the fields that are sent are changed. `stock: null` stops
    tracking stock, `allergens: null` takes the review back, and `sizes: null`
    gives the item one price; leaving any of them out leaves it as it is.
    `sizes` also sets the price, to the cheapest."""

    name_en: Annotated[Name, StringConstraints(min_length=1)] | None = None
    name_fr: Name | None = None
    description_en: Description | None = None
    description_fr: Description | None = None
    category_code: str | None = None
    base_price_cents: int | None = Field(default=None, ge=0, le=MAX_PRICE_CENTS)
    stock: int | None = Field(default=None, ge=0, le=MAX_STOCK)
    allergens: AllergenReview | None = None
    sizes: Sizes | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def check_required_fields_are_not_cleared(self) -> Self:
        for field in ("name_en", "category_code", "base_price_cents", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        return self

    @model_validator(mode="after")
    def check_price_is_the_cheapest_size(self) -> Self:
        price = self.base_price_cents
        if self.sizes and price is not None and price != _cheapest(self.sizes):
            raise ValueError("base_price_cents must be the cheapest size")
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


@dataclass(frozen=True)
class _Declaration:
    option_id: uuid.UUID | None
    allergen: Allergen
    level: AllergenLevel
    note: dict[str, str] | None


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


async def create_item(db: AsyncSession, new: NewItem, *, today: date) -> AdminItemView:
    """Add an item at the end of its category. Its code is made from its
    English name and never changes after. `today` dates any allergen review."""
    category_id = await _category_id(db, new.category_code)
    await _check_name_is_free(db, new.name_en, None)
    item = MenuItem(
        code=await free_code(db, MenuItem, new.name_en),
        category_id=category_id,
        name_i18n=_localized(new.name_en, new.name_fr),
        description_i18n=_description(new.description_en, new.description_fr),
        base_price_cents=new.base_price_cents,
        sort_order=await _next_position(db, category_id),
    )
    db.add(item)
    await db.flush()
    if new.stock is not None:
        await inventory.set_on_hand(db, item.code, new.stock)
    if new.allergens is not None:
        await _set_allergens(db, item, new.allergens, today)
    if new.sizes is not None:
        await _set_sizes(db, item, new.sizes)
    return await read_item(db, item.code)


async def update_item(
    db: AsyncSession, item_code: str, changes: ItemChanges, *, today: date
) -> AdminItemView:
    """Apply the fields that were sent. `today` dates any allergen review.
    Raises UnknownItem."""
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

    price = _cheapest(changes.sizes) if changes.sizes else changes.base_price_cents
    if price is not None:
        await _check_price_leaves_something_to_pay(db, item, price)
    if "sizes" in sent:
        await _set_sizes(db, item, changes.sizes)
    if price is not None:
        item.base_price_cents = price

    if changes.is_active is not None:
        item.is_active = changes.is_active

    if "stock" in sent:
        if changes.stock is None:
            await db.execute(delete(Inventory).where(Inventory.item_id == item.id))
        else:
            await inventory.set_on_hand(db, item.code, changes.stock)

    if "allergens" in sent:
        await _set_allergens(db, item, changes.allergens, today)

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


async def _set_allergens(
    db: AsyncSession, item: MenuItem, review: AllergenReview | None, today: date
) -> None:
    """Replace what the item itself declares; None takes the review back.
    What its options add stays: those come from the menu file."""
    removed = await db.execute(
        delete(AllergenDeclaration)
        .where(
            AllergenDeclaration.item_id == item.id,
            AllergenDeclaration.option_id.is_(None),
        )
        .returning(AllergenDeclaration.allergen, AllergenDeclaration.note_i18n)
    )
    notes = {allergen: note for allergen, note in removed.all()}
    item.allergens_reviewed_on = None if review is None else today
    if review is None:
        return
    db.add_all(
        AllergenDeclaration(
            item_id=item.id, allergen=allergen, level=AllergenLevel.CONTAINS
        )
        for allergen in review.contains
    )
    db.add_all(
        AllergenDeclaration(
            item_id=item.id,
            allergen=entry.allergen,
            level=AllergenLevel.MAY_CONTAIN,
            note_i18n=_reason(notes.get(entry.allergen), entry.because),
        )
        for entry in review.may_contain
    )
    await db.flush()


async def _set_sizes(
    db: AsyncSession, item: MenuItem, sizes: list[Size] | None
) -> None:
    """Make the item's size group match; None leaves it with one price. A
    size keeps its identity by its name, so one removed and typed again comes
    back, with its other languages. Nothing is deleted: carts point at these."""
    group = await db.scalar(
        select(OptionGroup)
        .where(OptionGroup.item_id == item.id, OptionGroup.code == _SIZE_GROUP_CODE)
        .options(selectinload(OptionGroup.options))
        .execution_options(populate_existing=True)
    )
    if sizes is None:
        if group is not None:
            # Required with nothing to choose, nobody could order the item.
            group.min_select = 0
            for option in group.options:
                option.is_active = option.is_default = False
        return
    if group is None:
        group = OptionGroup(
            item_id=item.id,
            code=_SIZE_GROUP_CODE,
            name_i18n=_SIZE_GROUP_NAME,
            min_select=1,
            max_select=1,
            sort_order=await _next_group_position(db, item.id),
            options=[],
        )
        db.add(group)

    group.min_select = group.max_select = 1
    # Cleared first: one default per group, so moving it would collide.
    for option in group.options:
        option.is_default = False
    await db.flush()

    cheapest = _cheapest(sizes)
    existing = {option.code: option for option in group.options}
    wanted = {code_from_name(size.name_en): size for size in sizes}
    for position, (code, size) in enumerate(wanted.items()):
        option = existing.get(code)
        if option is None:
            option = Option(code=code, name_i18n={"en": size.name_en})
            group.options.append(option)
        else:
            option.name_i18n = {**option.name_i18n, "en": size.name_en}
        option.price_delta_cents = size.price_cents - cheapest
        option.is_default = size.is_default
        option.is_active = True
        option.sort_order = position
    for code, option in existing.items():
        if code not in wanted and option.is_active:
            option.is_active = False
            # What it costs a car that already has it stays the same, as far
            # as an option's price can say so.
            real_price = item.base_price_cents + option.price_delta_cents
            option.price_delta_cents = max(real_price - cheapest, 0)
    await db.flush()


async def _next_group_position(db: AsyncSession, item_id: uuid.UUID) -> int:
    highest = await db.scalar(
        select(func.max(OptionGroup.sort_order)).where(OptionGroup.item_id == item_id)
    )
    return 0 if highest is None else highest + 1


def _reason(old: dict[str, str] | None, because: str) -> dict[str, str]:
    """The reason as a guest hears it. A reason left as it was keeps its other
    languages; a new one has only English, which the agent falls back to."""
    return old if old and old["en"] == because else {"en": because}


def code_from_name(name: str) -> str:
    """A code made from a name: "Crème brûlée!" becomes "creme_brulee"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    code = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")
    return code[:_CODE_LENGTH].rstrip("_") or "item"


async def free_code(
    db: AsyncSession, model: type[MenuItem] | type[ComboRule], name: str
) -> str:
    """A code made from a name that no row of `model` has yet."""
    base = code_from_name(name)
    taken = set(
        (await db.execute(select(model.code).where(model.code.startswith(base))))
        .scalars()
        .all()
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


async def ensure_category(db: AsyncSession, name: str) -> tuple[str, bool]:
    """The code of the category with this name, added at the end of the menu
    if there isn't one. Says which: (code, whether it is new)."""
    code = code_from_name(name)
    found = await db.scalar(select(MenuCategory.id).where(MenuCategory.code == code))
    if found is not None:
        return code, False
    highest = await db.scalar(select(func.max(MenuCategory.sort_order)))
    db.add(
        MenuCategory(
            code=code,
            name_i18n={"en": name},
            sort_order=0 if highest is None else highest + 1,
        )
    )
    await db.flush()
    return code, True


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
    declarations: dict[uuid.UUID, list[_Declaration]] = {}
    for row in await db.execute(
        select(
            AllergenDeclaration.item_id,
            AllergenDeclaration.option_id,
            AllergenDeclaration.allergen,
            AllergenDeclaration.level,
            AllergenDeclaration.note_i18n,
        ).where(AllergenDeclaration.item_id.in_(ids))
    ):
        declarations.setdefault(row.item_id, []).append(
            _Declaration(row.option_id, row.allergen, row.level, row.note_i18n)
        )
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
            allergens=_allergens_view(item, declarations.get(item.id, [])),
            option_allergens=_option_allergens(item, declarations.get(item.id, [])),
            sizes=_sizes_view(item),
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
                if group.code != _SIZE_GROUP_CODE
                and any(option.is_active for option in group.options)
            ],
        )
        for item in items
    ]


def _sizes_view(item: MenuItem) -> list[AdminSize]:
    return [
        AdminSize(
            name_en=localize(option.name_i18n, Language.EN),
            price_cents=item.base_price_cents + option.price_delta_cents,
            is_default=option.is_default,
        )
        for group in item.option_groups
        if group.code == _SIZE_GROUP_CODE
        for option in group.options
        if option.is_active
    ]


def _allergens_view(
    item: MenuItem, declarations: list[_Declaration]
) -> AdminAllergens | None:
    """What the item itself declares, or None if it was never reviewed."""
    if item.allergens_reviewed_on is None:
        return None
    own = sorted(
        (d for d in declarations if d.option_id is None),
        key=lambda d: _ALLERGEN_ORDER[d.allergen],
    )
    return AdminAllergens(
        contains=[d.allergen for d in own if d.level is AllergenLevel.CONTAINS],
        may_contain=[
            AdminMayContain(allergen=d.allergen, because=(d.note or {}).get("en", ""))
            for d in own
            if d.level is AllergenLevel.MAY_CONTAIN
        ],
    )


def _option_allergens(
    item: MenuItem, declarations: list[_Declaration]
) -> list[AdminOptionAllergens]:
    views: list[AdminOptionAllergens] = []
    for group in item.option_groups:
        for option in group.options:
            adds = [d for d in declarations if d.option_id == option.id]
            if adds and option.is_active:
                views.append(
                    AdminOptionAllergens(
                        option_name=localize(option.name_i18n, Language.EN),
                        contains=[
                            d.allergen
                            for d in adds
                            if d.level is AllergenLevel.CONTAINS
                        ],
                        may_contain=[
                            d.allergen
                            for d in adds
                            if d.level is AllergenLevel.MAY_CONTAIN
                        ],
                    )
                )
    return views
