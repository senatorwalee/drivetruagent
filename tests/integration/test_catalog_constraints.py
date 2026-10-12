"""The database itself must refuse invalid menu data.

Each rejection test names the constraint it expects, so a test cannot pass
because some other rule happened to fire.
"""

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    Option,
    OptionGroup,
)
from drivetruagent.enums import Language
from tests.integration.factories import (
    make_category,
    make_group,
    make_item,
    make_option,
)

FRENCH_ONLY = {"fr": "Frites"}


async def count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- Defaults ---------------------------------------------------------------


async def test_database_fills_in_defaults(db_session: AsyncSession) -> None:
    item = await make_item(db_session)

    assert item.id is not None
    assert item.is_active is True
    assert item.sort_order == 0
    assert item.description_i18n is None
    assert item.created_at is not None


async def test_updated_at_is_readable_after_an_update(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)

    item.base_price_cents = 299
    await db_session.flush()

    # Would raise if SQLAlchemy tried to lazy-load the refreshed value.
    assert item.updated_at >= item.created_at


# --- Items and categories ---------------------------------------------------


async def test_duplicate_item_code_is_rejected(db_session: AsyncSession) -> None:
    await make_item(db_session, code="fries")

    with pytest.raises(IntegrityError, match="uq_menu_items_code"):
        await make_item(db_session, code="fries")


async def test_duplicate_category_code_is_rejected(db_session: AsyncSession) -> None:
    await make_category(db_session, code="sides")

    with pytest.raises(IntegrityError, match="uq_menu_categories_code"):
        await make_category(db_session, code="sides")


async def test_negative_base_price_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(
        IntegrityError, match="ck_menu_items_base_price_cents_non_negative"
    ):
        await make_item(db_session, base_price_cents=-1)


async def test_free_item_is_allowed(db_session: AsyncSession) -> None:
    item = await make_item(db_session, base_price_cents=0)

    assert item.base_price_cents == 0


async def test_category_with_items_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    category = await make_category(db_session)
    await make_item(db_session, category)

    with pytest.raises(
        IntegrityError, match="fk_menu_items_category_id_menu_categories"
    ):
        await db_session.execute(
            delete(MenuCategory).where(MenuCategory.id == category.id)
        )


# --- Translated names -------------------------------------------------------


async def test_category_name_without_english_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_menu_categories_name_i18n_has_en"):
        await make_category(db_session, name_i18n=FRENCH_ONLY)


async def test_item_name_without_english_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_menu_items_name_i18n_has_en"):
        await make_item(db_session, name_i18n=FRENCH_ONLY)


async def test_item_description_without_english_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_menu_items_description_i18n_has_en"):
        await make_item(db_session, description_i18n=FRENCH_ONLY)


async def test_group_name_without_english_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_option_groups_name_i18n_has_en"):
        await make_group(db_session, name_i18n=FRENCH_ONLY)


async def test_option_name_without_english_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_options_name_i18n_has_en"):
        await make_option(db_session, name_i18n=FRENCH_ONLY)


async def test_english_name_must_be_text(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_menu_items_name_i18n_has_en"):
        await make_item(db_session, name_i18n={"en": 5})


async def test_missing_description_is_stored_as_sql_null(
    db_session: AsyncSession,
) -> None:
    """An explicit None must become NULL, not the JSON value `null`."""
    item = await make_item(db_session, description_i18n=None)

    is_null = await db_session.execute(
        text("SELECT description_i18n IS NULL FROM menu_items WHERE id = :id"),
        {"id": item.id},
    )

    assert is_null.scalar_one() is True


# --- Option groups ----------------------------------------------------------


async def test_duplicate_group_code_on_one_item_is_rejected(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    await make_group(db_session, item, code="size")

    with pytest.raises(IntegrityError, match="uq_option_groups_item_id_code"):
        await make_group(db_session, item, code="size")


async def test_same_group_code_on_two_items_is_allowed(
    db_session: AsyncSession,
) -> None:
    await make_group(db_session, await make_item(db_session), code="size")
    await make_group(db_session, await make_item(db_session), code="size")

    assert await count(db_session, OptionGroup) == 2


async def test_min_select_above_max_select_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_option_groups_max_select_covers_min"):
        await make_group(db_session, min_select=3, max_select=2)


async def test_max_select_of_zero_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_option_groups_max_select_positive"):
        await make_group(db_session, min_select=0, max_select=0)


async def test_negative_min_select_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(
        IntegrityError, match="ck_option_groups_min_select_non_negative"
    ):
        await make_group(db_session, min_select=-1, max_select=1)


# --- Options ----------------------------------------------------------------


async def test_duplicate_option_code_in_one_group_is_rejected(
    db_session: AsyncSession,
) -> None:
    group = await make_group(db_session)
    await make_option(db_session, group, code="small")

    with pytest.raises(IntegrityError, match="uq_options_option_group_id_code"):
        await make_option(db_session, group, code="small")


async def test_negative_price_delta_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(
        IntegrityError, match="ck_options_price_delta_cents_non_negative"
    ):
        await make_option(db_session, price_delta_cents=-50)


async def test_two_default_options_in_one_group_are_rejected(
    db_session: AsyncSession,
) -> None:
    group = await make_group(db_session)
    await make_option(db_session, group, is_default=True)

    with pytest.raises(IntegrityError, match="uq_options_one_default_per_group"):
        await make_option(db_session, group, is_default=True)


async def test_one_default_in_each_of_two_groups_is_allowed(
    db_session: AsyncSession,
) -> None:
    await make_option(db_session, await make_group(db_session), is_default=True)
    await make_option(db_session, await make_group(db_session), is_default=True)

    assert await count(db_session, Option) == 2


async def test_many_non_default_options_in_one_group_are_allowed(
    db_session: AsyncSession,
) -> None:
    group = await make_group(db_session)
    await make_option(db_session, group, is_default=True)
    await make_option(db_session, group)
    await make_option(db_session, group)

    assert await count(db_session, Option) == 3


# --- Aliases ----------------------------------------------------------------


async def test_alias_in_an_unsupported_language_is_rejected(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)

    # Raw SQL: the ORM would refuse "de" in Python before the database saw it.
    with pytest.raises(IntegrityError, match="ck_menu_item_aliases_language_valid"):
        await db_session.execute(
            text(
                "INSERT INTO menu_item_aliases (item_id, language, alias) "
                "VALUES (:item_id, 'de', 'pommes')"
            ),
            {"item_id": item.id},
        )


@pytest.mark.parametrize("alias", ["Coke", " coke", "coke "])
async def test_alias_that_is_not_lowercase_and_trimmed_is_rejected(
    db_session: AsyncSession, alias: str
) -> None:
    item = await make_item(db_session)
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.EN, alias=alias))

    with pytest.raises(IntegrityError, match="ck_menu_item_aliases_alias_normalised"):
        await db_session.flush()


async def test_empty_alias_is_rejected(db_session: AsyncSession) -> None:
    item = await make_item(db_session)
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.EN, alias=""))

    with pytest.raises(IntegrityError, match="ck_menu_item_aliases_alias_not_empty"):
        await db_session.flush()


async def test_duplicate_alias_for_one_item_is_rejected(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.EN, alias="chips"))
    await db_session.flush()
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.EN, alias="chips"))

    with pytest.raises(
        IntegrityError, match="uq_menu_item_aliases_item_id_language_alias"
    ):
        await db_session.flush()


async def test_same_alias_may_point_at_several_items(
    db_session: AsyncSession,
) -> None:
    for _ in range(2):
        item = await make_item(db_session)
        db_session.add(
            MenuItemAlias(item_id=item.id, language=Language.EN, alias="burger")
        )
    await db_session.flush()

    assert await count(db_session, MenuItemAlias) == 2


async def test_alias_language_loads_as_an_enum(db_session: AsyncSession) -> None:
    item = await make_item(db_session)
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.FR, alias="frites"))
    await db_session.flush()
    db_session.expunge_all()

    alias = (await db_session.execute(select(MenuItemAlias))).scalar_one()

    assert alias.language is Language.FR


# --- Cascades ---------------------------------------------------------------


async def test_deleting_an_item_removes_its_groups_options_and_aliases(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    group = await make_group(db_session, item)
    await make_option(db_session, group)
    db_session.add(MenuItemAlias(item_id=item.id, language=Language.EN, alias="chips"))
    await db_session.flush()

    await db_session.execute(delete(MenuItem).where(MenuItem.id == item.id))

    assert await count(db_session, OptionGroup) == 0
    assert await count(db_session, Option) == 0
    assert await count(db_session, MenuItemAlias) == 0
