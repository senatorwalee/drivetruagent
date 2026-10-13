"""Loading the menu file into a real database."""

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    ComboRule,
    ComboRuleSlot,
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    Option,
    OptionGroup,
)
from drivetruagent.ordering.offers import load_combo_rules
from drivetruagent.seed import ComboSeed, MenuSeed, load_menu, seed_menu

MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "menu.json"
TABLES = [MenuCategory, MenuItem, OptionGroup, Option, MenuItemAlias]


def name(en: str, fr: str) -> dict[str, str]:
    return {"en": en, "fr": fr}


def small_menu(
    *, items: list[dict[str, Any]] | None = None, default_size: str = "small"
) -> MenuSeed:
    """A one-category menu: fries with a size group, plus a cookie."""
    fries = {
        "code": "fries",
        "name": name("Fries", "Frites"),
        "base_price_cents": 249,
        "aliases": {"en": ["chips"], "fr": ["frite"]},
        "option_groups": [
            {
                "code": "size",
                "name": name("Size", "Format"),
                "min_select": 1,
                "max_select": 1,
                "options": [
                    {
                        "code": "small",
                        "name": name("Small", "Petit"),
                        "default": default_size == "small",
                    },
                    {
                        "code": "large",
                        "name": name("Large", "Grand"),
                        "price_delta_cents": 130,
                        "default": default_size == "large",
                    },
                ],
            }
        ],
    }
    cookie = {
        "code": "cookie",
        "name": name("Cookie", "Biscuit"),
        "base_price_cents": 149,
    }
    return MenuSeed.model_validate(
        {
            "categories": [
                {
                    "code": "sides",
                    "name": name("Sides", "Accompagnements"),
                    "items": items if items is not None else [fries, cookie],
                }
            ]
        }
    )


async def row_counts(session: AsyncSession) -> dict[str, int]:
    return {
        model.__tablename__: (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()
        for model in TABLES
    }


async def item_by_code(session: AsyncSession, code: str) -> MenuItem:
    return (
        await session.execute(select(MenuItem).where(MenuItem.code == code))
    ).scalar_one()


async def option_by_code(session: AsyncSession, code: str) -> Option:
    return (
        await session.execute(select(Option).where(Option.code == code))
    ).scalar_one()


async def test_real_menu_file_loads(db_session: AsyncSession) -> None:
    summary = await seed_menu(db_session, load_menu(MENU_PATH))

    assert summary.categories == 4
    assert summary.items == 20
    assert await row_counts(db_session) == {
        "menu_categories": summary.categories,
        "menu_items": summary.items,
        "option_groups": summary.option_groups,
        "options": summary.options,
        "menu_item_aliases": summary.aliases,
    }


async def test_seeding_twice_changes_nothing(db_session: AsyncSession) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)
    counts_after_first = await row_counts(db_session)
    fries_id = (await item_by_code(db_session, "fries")).id

    await seed_menu(db_session, menu)

    assert await row_counts(db_session) == counts_after_first
    # The same row was updated, not replaced, so references to it stay valid.
    assert (await item_by_code(db_session, "fries")).id == fries_id


async def test_seed_updates_changed_values(db_session: AsyncSession) -> None:
    await seed_menu(db_session, small_menu())
    changed = small_menu()
    changed.categories[0].items[0].base_price_cents = 299

    await seed_menu(db_session, changed)
    db_session.expire_all()

    assert (await item_by_code(db_session, "fries")).base_price_cents == 299


async def test_item_removed_from_the_file_is_deactivated_not_deleted(
    db_session: AsyncSession,
) -> None:
    await seed_menu(db_session, small_menu())
    fries_only = small_menu()
    fries_only.categories[0].items.pop()  # drop the cookie

    await seed_menu(db_session, fries_only)
    db_session.expire_all()

    assert (await item_by_code(db_session, "cookie")).is_active is False
    assert (await item_by_code(db_session, "fries")).is_active is True


async def test_item_added_back_to_the_file_is_reactivated(
    db_session: AsyncSession,
) -> None:
    fries_only = small_menu()
    fries_only.categories[0].items.pop()
    await seed_menu(db_session, small_menu())
    await seed_menu(db_session, fries_only)

    await seed_menu(db_session, small_menu())
    db_session.expire_all()

    assert (await item_by_code(db_session, "cookie")).is_active is True


async def test_option_removed_from_the_file_is_deactivated(
    db_session: AsyncSession,
) -> None:
    await seed_menu(db_session, small_menu())
    without_large = small_menu()
    without_large.categories[0].items[0].option_groups[0].options.pop()

    await seed_menu(db_session, without_large)
    db_session.expire_all()

    assert (await option_by_code(db_session, "large")).is_active is False
    assert (await option_by_code(db_session, "small")).is_active is True


async def test_default_can_move_to_another_option(db_session: AsyncSession) -> None:
    await seed_menu(db_session, small_menu(default_size="small"))

    await seed_menu(db_session, small_menu(default_size="large"))
    db_session.expire_all()

    assert (await option_by_code(db_session, "small")).is_default is False
    assert (await option_by_code(db_session, "large")).is_default is True


async def test_group_removed_from_the_file_is_deleted(
    db_session: AsyncSession,
) -> None:
    await seed_menu(db_session, small_menu())
    without_groups = small_menu()
    without_groups.categories[0].items[0].option_groups.clear()

    await seed_menu(db_session, without_groups)

    counts = await row_counts(db_session)
    assert counts["option_groups"] == 0
    assert counts["options"] == 0


async def test_aliases_follow_the_file(db_session: AsyncSession) -> None:
    await seed_menu(db_session, small_menu())
    changed = small_menu()
    changed.categories[0].items[0].aliases = {}

    await seed_menu(db_session, changed)

    assert (await row_counts(db_session))["menu_item_aliases"] == 0


async def test_seed_sets_display_order_from_file_order(
    db_session: AsyncSession,
) -> None:
    await seed_menu(db_session, small_menu())

    assert (await item_by_code(db_session, "fries")).sort_order == 0
    assert (await item_by_code(db_session, "cookie")).sort_order == 1


# --- Combos -----------------------------------------------------------------


def a_combo(code: str, *slots: str, discount_cents: int = 50) -> ComboSeed:
    return ComboSeed.model_validate(
        {
            "code": code,
            "name": name(code.title(), code.title()),
            "discount_cents": discount_cents,
            "slots": list(slots),
        }
    )


async def combo_slots(session: AsyncSession) -> dict[str, list[str]]:
    """Each active combo's slot categories, in priority order."""
    return {
        rule.code: [slot.category_code for slot in rule.slots]
        for rule in await load_combo_rules(session)
    }


async def test_the_real_menus_combo_is_loaded(db_session: AsyncSession) -> None:
    summary = await seed_menu(db_session, load_menu(MENU_PATH))

    assert summary.combos == 1
    assert await combo_slots(db_session) == {
        "burger_combo": ["burgers", "sides", "drinks"]
    }


async def test_seeding_twice_keeps_the_same_combo(db_session: AsyncSession) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)
    combo_id = (await db_session.execute(select(ComboRule.id))).scalar_one()

    await seed_menu(db_session, menu)

    assert (await db_session.execute(select(ComboRule.id))).scalar_one() == combo_id
    slots = await db_session.execute(select(func.count()).select_from(ComboRuleSlot))
    assert slots.scalar_one() == 3


async def test_a_combo_removed_from_the_file_is_retired_not_deleted(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)

    await seed_menu(db_session, menu.model_copy(update={"combos": []}))
    db_session.expire_all()

    rule = (await db_session.execute(select(ComboRule))).scalar_one()
    assert rule.is_active is False
    assert await combo_slots(db_session) == {}


async def test_a_combo_added_back_to_the_file_applies_again(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)
    await seed_menu(db_session, menu.model_copy(update={"combos": []}))

    await seed_menu(db_session, menu)

    assert list(await combo_slots(db_session)) == ["burger_combo"]


async def test_a_combos_slots_and_saving_follow_the_file(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    await seed_menu(db_session, menu)
    changed = menu.model_copy(
        update={
            "combos": [
                a_combo("burger_combo", "burgers", "desserts", discount_cents=75)
            ]
        }
    )

    await seed_menu(db_session, changed)

    (rule,) = await load_combo_rules(db_session)
    assert [slot.category_code for slot in rule.slots] == ["burgers", "desserts"]
    assert rule.discount_cents == 75


async def test_combo_priority_is_the_order_in_the_file(
    db_session: AsyncSession,
) -> None:
    menu = load_menu(MENU_PATH)
    sweet_deal = a_combo("sweet_deal", "drinks", "desserts")
    burger_combo = menu.combos[0]

    await seed_menu(
        db_session, menu.model_copy(update={"combos": [sweet_deal, burger_combo]})
    )
    assert list(await combo_slots(db_session)) == ["sweet_deal", "burger_combo"]

    await seed_menu(
        db_session, menu.model_copy(update={"combos": [burger_combo, sweet_deal]})
    )
    assert list(await combo_slots(db_session)) == ["burger_combo", "sweet_deal"]
