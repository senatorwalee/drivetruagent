"""Bringing a restaurant on: everything it entered, saved as one.

The onboarding page keeps the whole setup to itself until the manager goes
live, then sends it here. It all lands in one transaction, like the seed: a
refusal leaves the menu as it was, so a guest never meets half of one.

Items go through `menu_admin.create_item`, so names are checked and codes made
the way the menu page does it. An item sent with no allergen review stays
unreviewed, so the agent sends those questions to a person.

Nothing here commits. The caller owns the transaction.
"""

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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    ComboRule,
    ComboRuleSlot,
    DietSuitability,
    MenuCategory,
    MenuItem,
)
from drivetruagent.enums import Diet, DietStatus
from drivetruagent.ordering import menu_admin
from drivetruagent.ordering.errors import (
    ComboNeedsItems,
    ComboSavingTooLarge,
    DuplicateComboName,
)
from drivetruagent.ordering.menu_admin import (
    MAX_PRICE_CENTS,
    Name,
    NewItem,
    NewItemFields,
)
from drivetruagent.ordering.restaurant import RestaurantDetails, save_profile

SectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
]
# How each diet is written in each language, for the notes below.
_DIET_NAMES = {Diet.HALAL: ("halal", "halal"), Diet.KOSHER: ("kosher", "casher")}


class DietReview(BaseModel):
    """How the restaurant reviewed an item for a diet. An item with no review
    has none on file, and nothing is claimed about it."""

    model_config = ConfigDict(extra="forbid")

    status: DietStatus
    # Why, in words a guest can hear. The agent reads it out.
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] | (
        None
    ) = None
    # Who issued the certificate. Needed for "certified": the agent repeats
    # the claim to guests, so it has to have a name behind it.
    certified_by: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None
    ) = None

    @field_validator("note", "certified_by", mode="after")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def check_certified_says_by_whom(self) -> Self:
        if self.status is DietStatus.CERTIFIED and not self.certified_by:
            raise ValueError("certified needs certified_by")
        if self.status is not DietStatus.CERTIFIED and self.certified_by:
            raise ValueError("only a certified item has certified_by")
        return self


class OnboardingItem(NewItemFields):
    # The section of the menu it goes in. New ones are created.
    section: SectionName
    diets: dict[Diet, DietReview] = Field(default_factory=dict)


class OnboardingCombo(BaseModel):
    """One item from each part, bought together, costs less."""

    model_config = ConfigDict(extra="forbid")

    name_en: Annotated[Name, StringConstraints(min_length=1)]
    name_fr: Name | None = None
    # Taken off each time the combo is made.
    discount_cents: int = Field(gt=0, le=MAX_PRICE_CENTS)
    # Section names. The first is the main item the combo is built around.
    slots: list[SectionName] = Field(min_length=2)


class Onboarding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant: RestaurantDetails
    # True: what is on the menu now comes off. False: this is added to it.
    replace_menu: bool = False
    items: list[OnboardingItem] = Field(min_length=1)
    # In priority order: when units could count toward two combos, the
    # earlier one gets them.
    combos: list[OnboardingCombo] = Field(default_factory=list)


class OnboardingResult(BaseModel):
    restaurant_name: str
    items: int
    sections_added: int
    combos: int
    diet_reviews: int
    allergen_reviews: int
    # What `replace_menu` took off. Past orders keep them.
    items_taken_off: int
    combos_retired: int


async def go_live(
    db: AsyncSession, setup: Onboarding, *, today: date
) -> OnboardingResult:
    """Save the whole setup. `today` dates the allergen reviews. Raises
    DuplicateItemName, DuplicateComboName, ComboNeedsItems or
    ComboSavingTooLarge, and the caller then rolls back."""
    taken_off, retired = await _clear_menu(db) if setup.replace_menu else (0, 0)
    await save_profile(db, setup.restaurant)

    sections_added = diet_reviews = 0
    for entry in setup.items:
        code, created = await menu_admin.ensure_category(db, entry.section)
        sections_added += created
        fields = entry.model_dump(exclude={"section", "diets"})
        item = await menu_admin.create_item(
            db, NewItem(**fields, category_code=code), today=today
        )
        item_id = await db.scalar(
            select(MenuItem.id).where(MenuItem.code == item.item_code)
        )
        assert item_id is not None  # create_item just added it
        for diet, review in entry.diets.items():
            db.add(
                DietSuitability(
                    item_id=item_id,
                    diet=diet,
                    status=review.status,
                    note_i18n=_diet_note(diet, review),
                )
            )
            diet_reviews += 1
    await db.flush()

    for combo in setup.combos:
        await _add_combo(db, combo)

    return OnboardingResult(
        restaurant_name=setup.restaurant.name,
        items=len(setup.items),
        sections_added=sections_added,
        combos=len(setup.combos),
        diet_reviews=diet_reviews,
        allergen_reviews=sum(entry.allergens is not None for entry in setup.items),
        items_taken_off=taken_off,
        combos_retired=retired,
    )


async def _clear_menu(db: AsyncSession) -> tuple[int, int]:
    """Take every item off the menu and retire every combo. Nothing is
    deleted, because past orders point at them. Says how many of each."""
    items = await db.execute(
        update(MenuItem)
        .where(MenuItem.is_active)
        .values(is_active=False, updated_at=func.now())
        .returning(MenuItem.id)
    )
    combos = await db.execute(
        update(ComboRule)
        .where(ComboRule.is_active)
        .values(is_active=False, updated_at=func.now())
        .returning(ComboRule.id)
    )
    return len(items.all()), len(combos.all())


async def _add_combo(db: AsyncSession, combo: OnboardingCombo) -> None:
    await _check_combo_name_is_free(db, combo.name_en)
    category_ids, cheapest = await _categories_with_items(db)

    cheapest_set = 0
    slot_ids: list[uuid.UUID] = []
    for section in combo.slots:
        code = menu_admin.code_from_name(section)
        if code not in cheapest:
            raise ComboNeedsItems(combo.name_en, section)
        cheapest_set += cheapest[code]
        slot_ids.append(category_ids[code])
    # The cheapest set a customer could build. A saving as large as that
    # would make food free. The seed checks the same.
    if combo.discount_cents >= cheapest_set:
        raise ComboSavingTooLarge(combo.name_en, combo.discount_cents, cheapest_set)

    highest = await db.scalar(select(func.max(ComboRule.sort_order)))
    rule = ComboRule(
        code=await menu_admin.free_code(db, ComboRule, combo.name_en),
        name_i18n=(
            {"en": combo.name_en, "fr": combo.name_fr}
            if combo.name_fr
            else {"en": combo.name_en}
        ),
        discount_cents=combo.discount_cents,
        sort_order=0 if highest is None else highest + 1,
    )
    db.add(rule)
    await db.flush()
    db.add_all(
        ComboRuleSlot(combo_rule_id=rule.id, position=position, category_id=category)
        for position, category in enumerate(slot_ids)
    )
    await db.flush()


async def _check_combo_name_is_free(db: AsyncSession, name_en: str) -> None:
    other = await db.scalar(
        select(ComboRule.code)
        .where(
            ComboRule.is_active,
            func.lower(ComboRule.name_i18n["en"].astext) == name_en.casefold(),
        )
        .limit(1)
    )
    if other is not None:
        raise DuplicateComboName(name_en)


async def _categories_with_items(
    db: AsyncSession,
) -> tuple[dict[str, uuid.UUID], dict[str, int]]:
    """Categories that have something on the menu, by code: their ids, and
    the price of the cheapest item in each."""
    rows = (
        await db.execute(
            select(
                MenuCategory.code,
                MenuCategory.id,
                func.min(MenuItem.base_price_cents),
            )
            .join(MenuItem, MenuItem.category_id == MenuCategory.id)
            .where(MenuItem.is_active)
            .group_by(MenuCategory.code, MenuCategory.id)
        )
    ).all()
    return (
        {code: category_id for code, category_id, _ in rows},
        {code: cheapest for code, _, cheapest in rows},
    )


def _diet_note(diet: Diet, review: DietReview) -> dict[str, str]:
    """Why, in words a guest can hear. A typed reason is kept as it is; the
    agent falls back to it in French too."""
    english, french = _DIET_NAMES[diet]
    if review.status is DietStatus.CERTIFIED:
        by = review.certified_by
        return {
            "en": f"Certified {english} by {by}.",
            "fr": f"Certifié {french} par {by}.",
        }
    if review.note:
        return {"en": review.note}
    if review.status is DietStatus.SUITABLE:
        return {
            "en": f"Nothing in it is forbidden for {english}. It is prepared apart.",
            "fr": f"Rien d'interdit pour un repas {french}, et préparé à part.",
        }
    return {
        "en": f"It is not prepared to {english} rules.",
        "fr": f"Ce n'est pas préparé selon les règles {french}.",
    }
