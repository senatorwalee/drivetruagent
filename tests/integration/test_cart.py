"""Cart rules against a real database and the real menu.

Prices used below, from data/menu.json:
  fries 249 (medium +70, large +130; curly +50), cola 199 (medium +40),
  chicken nuggets 449 (9 pieces +200), cookie 149, apple pie 199.
"""

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    CartLine,
    CartLineOption,
    CustomerSession,
    MenuItem,
    Option,
)
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.cart import add_item, get_cart, remove_line, update_line
from drivetruagent.ordering.errors import (
    InvalidQuantity,
    ItemUnavailable,
    MissingRequiredOptions,
    NothingToUpdate,
    OptionUnavailable,
    SessionEnded,
    SessionNotFound,
    TooManyOptions,
    UnknownCartLine,
    UnknownItem,
    UnknownOption,
)
from drivetruagent.ordering.sessions import end_session, open_session
from drivetruagent.ordering.views import CartSnapshot, LineView

TAX = Decimal("0.13")

pytestmark = pytest.mark.usefixtures("menu")


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    """An open session. Asking for `menu` here makes the menu load first."""
    return (await open_session(db_session)).id


def options_of(line: LineView) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for option in line.options:
        result.setdefault(option.group_code, []).append(option.option_code)
    return result


async def row_count(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# --- Adding -----------------------------------------------------------------


async def test_fries_without_a_size_are_refused_and_nothing_is_written(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(MissingRequiredOptions) as error:
        await add_item(db_session, session_id, "fries", tax_rate=TAX)

    missing = error.value.details["missing"]
    assert [group["group_code"] for group in missing] == ["size"]
    assert [option["option_code"] for option in missing[0]["options"]] == [
        "small",
        "medium",
        "large",
    ]
    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.lines == []
    assert cart.version == 0
    assert cart.state is SessionState.GREETING


async def test_fries_with_a_size_get_the_default_style_and_seasoning(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await add_item(
        db_session, session_id, "fries", selections={"size": ["large"]}, tax_rate=TAX
    )

    (line,) = cart.lines
    assert line.line_no == 1
    assert line.item_code == "fries"
    assert line.item_name == "Fries"
    assert line.quantity == 1
    assert options_of(line) == {
        "size": ["large"],
        "style": ["regular"],
        "seasoning": ["plain"],
    }
    assert line.unit_price_cents == 379
    assert line.line_total_cents == 379


async def test_item_without_choices_can_be_added_directly(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await add_item(db_session, session_id, "cookie", quantity=3, tax_rate=TAX)

    (line,) = cart.lines
    assert line.options == []
    assert line.line_total_cents == 447


async def test_adding_moves_the_session_to_ordering_and_bumps_the_version(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    assert cart.state is SessionState.ORDERING
    assert cart.version == 1


async def test_each_add_creates_a_new_line_even_for_the_same_item(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)
    cart = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    assert [line.line_no for line in cart.lines] == [1, 2]
    assert cart.version == 2


async def test_cart_totals_cover_every_line(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(
        db_session,
        session_id,
        "fries",
        quantity=2,
        selections={"size": ["large"]},
        tax_rate=TAX,
    )
    cart = await add_item(
        db_session, session_id, "cola", selections={"size": ["medium"]}, tax_rate=TAX
    )

    # 2 x 379 + 239 = 997; tax 129.61 rounds to 130.
    assert cart.item_count == 3
    assert cart.subtotal_cents == 997
    assert cart.tax_cents == 130
    assert cart.total_cents == 1127


async def test_unknown_item_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(UnknownItem) as error:
        await add_item(db_session, session_id, "lobster", tax_rate=TAX)

    assert error.value.details == {"item_code": "lobster"}


async def test_inactive_item_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(is_active=False)
    )

    with pytest.raises(ItemUnavailable):
        await add_item(db_session, session_id, "cookie", tax_rate=TAX)


async def test_inactive_option_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(
        update(Option).where(Option.code == "curly").values(is_active=False)
    )

    with pytest.raises(OptionUnavailable) as error:
        await add_item(
            db_session,
            session_id,
            "fries",
            selections={"size": ["small"], "style": ["curly"]},
            tax_rate=TAX,
        )

    assert error.value.details["valid_options"] == ["regular", "waffle"]


async def test_option_from_another_item_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Vanilla is a milkshake flavour, not a fries size."""
    with pytest.raises(UnknownOption):
        await add_item(
            db_session,
            session_id,
            "fries",
            selections={"size": ["vanilla"]},
            tax_rate=TAX,
        )


@pytest.mark.parametrize("quantity", [0, -1])
async def test_quantity_below_1_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID, quantity: int
) -> None:
    with pytest.raises(InvalidQuantity):
        await add_item(
            db_session, session_id, "cookie", quantity=quantity, tax_rate=TAX
        )


async def test_a_refused_add_leaves_the_cart_exactly_as_it_was(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    before = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    with pytest.raises(TooManyOptions):
        await add_item(
            db_session,
            session_id,
            "chicken_nuggets",
            selections={
                "count": ["6_pieces"],
                "dip": ["ketchup", "barbecue", "sweet_chili"],
            },
            tax_rate=TAX,
        )

    assert await get_cart(db_session, session_id, tax_rate=TAX) == before
    assert await row_count(db_session, CartLine) == 1


async def test_unknown_session_is_reported(db_session: AsyncSession) -> None:
    with pytest.raises(SessionNotFound):
        await add_item(db_session, uuid.uuid4(), "cookie", tax_rate=TAX)


# --- Updating ---------------------------------------------------------------


@pytest.fixture
async def cart_with_fries(db_session: AsyncSession, session_id: uuid.UUID) -> None:
    """Line 1: one small curly fries."""
    await add_item(
        db_session,
        session_id,
        "fries",
        selections={"size": ["small"], "style": ["curly"]},
        tax_rate=TAX,
    )


@pytest.mark.usefixtures("cart_with_fries")
async def test_changing_only_the_quantity_keeps_the_options(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await update_line(db_session, session_id, 1, quantity=2, tax_rate=TAX)

    (line,) = cart.lines
    assert line.quantity == 2
    assert options_of(line)["style"] == ["curly"]
    assert line.line_total_cents == 2 * 299
    assert cart.version == 2


@pytest.mark.usefixtures("cart_with_fries")
async def test_changing_one_group_keeps_the_others(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "Make that a large" must not reset curly back to regular."""
    cart = await update_line(
        db_session, session_id, 1, selections={"size": ["large"]}, tax_rate=TAX
    )

    (line,) = cart.lines
    assert options_of(line) == {
        "size": ["large"],
        "style": ["curly"],
        "seasoning": ["plain"],
    }
    assert line.unit_price_cents == 249 + 130 + 50


@pytest.mark.usefixtures("cart_with_fries")
async def test_quantity_and_options_can_change_together(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await update_line(
        db_session,
        session_id,
        1,
        quantity=3,
        selections={"style": ["waffle"]},
        tax_rate=TAX,
    )

    (line,) = cart.lines
    assert line.quantity == 3
    assert options_of(line)["style"] == ["waffle"]
    assert await row_count(db_session, CartLineOption) == 3


@pytest.mark.usefixtures("cart_with_fries")
async def test_clearing_a_defaulted_group_returns_it_to_the_default(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await update_line(
        db_session, session_id, 1, selections={"style": []}, tax_rate=TAX
    )

    assert options_of(cart.lines[0])["style"] == ["regular"]


@pytest.mark.usefixtures("cart_with_fries")
async def test_clearing_a_required_group_is_refused_and_changes_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(MissingRequiredOptions):
        await update_line(
            db_session, session_id, 1, selections={"size": []}, tax_rate=TAX
        )

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert options_of(cart.lines[0])["size"] == ["small"]
    assert cart.version == 1


async def test_optional_choices_can_be_added_and_cleared(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(
        db_session,
        session_id,
        "chicken_nuggets",
        selections={"count": ["9_pieces"]},
        tax_rate=TAX,
    )

    with_dips = await update_line(
        db_session,
        session_id,
        1,
        selections={"dip": ["ketchup", "barbecue"]},
        tax_rate=TAX,
    )
    without_dips = await update_line(
        db_session, session_id, 1, selections={"dip": []}, tax_rate=TAX
    )

    assert options_of(with_dips.lines[0])["dip"] == ["ketchup", "barbecue"]
    assert options_of(without_dips.lines[0]) == {"count": ["9_pieces"]}


@pytest.mark.usefixtures("cart_with_fries")
async def test_update_with_nothing_to_change_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(NothingToUpdate):
        await update_line(db_session, session_id, 1, tax_rate=TAX)


@pytest.mark.usefixtures("cart_with_fries")
async def test_update_to_quantity_0_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(InvalidQuantity):
        await update_line(db_session, session_id, 1, quantity=0, tax_rate=TAX)


@pytest.mark.usefixtures("cart_with_fries")
async def test_update_of_a_line_that_does_not_exist_lists_the_real_ones(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(UnknownCartLine) as error:
        await update_line(db_session, session_id, 7, quantity=2, tax_rate=TAX)

    assert error.value.details == {"line_no": 7, "existing_lines": [1]}


# --- Removing ---------------------------------------------------------------


async def three_lines(db: AsyncSession, session_id: uuid.UUID) -> CartSnapshot:
    await add_item(db, session_id, "cookie", tax_rate=TAX)
    await add_item(db, session_id, "apple_pie", tax_rate=TAX)
    return await add_item(db, session_id, "bottled_water", tax_rate=TAX)


async def test_removing_a_line_leaves_the_other_numbers_alone(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await three_lines(db_session, session_id)

    cart = await remove_line(db_session, session_id, 2, tax_rate=TAX)

    assert [(line.line_no, line.item_code) for line in cart.lines] == [
        (1, "cookie"),
        (3, "bottled_water"),
    ]
    assert cart.subtotal_cents == 149 + 149
    assert cart.version == 4


async def test_a_removed_line_number_is_never_reused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await three_lines(db_session, session_id)
    await remove_line(db_session, session_id, 3, tax_rate=TAX)

    cart = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    assert [line.line_no for line in cart.lines] == [1, 2, 4]


async def test_removing_a_line_removes_its_chosen_options(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(
        db_session, session_id, "fries", selections={"size": ["small"]}, tax_rate=TAX
    )

    await remove_line(db_session, session_id, 1, tax_rate=TAX)

    assert await row_count(db_session, CartLine) == 0
    assert await row_count(db_session, CartLineOption) == 0


async def test_removing_the_last_line_leaves_an_empty_cart(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    cart = await remove_line(db_session, session_id, 1, tax_rate=TAX)

    assert cart.lines == []
    assert cart.item_count == 0
    assert cart.total_cents == 0


async def test_removing_a_line_that_does_not_exist_is_refused(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await three_lines(db_session, session_id)

    with pytest.raises(UnknownCartLine) as error:
        await remove_line(db_session, session_id, 9, tax_rate=TAX)

    assert error.value.details["existing_lines"] == [1, 2, 3]


# --- Reading ----------------------------------------------------------------


async def test_names_are_in_french_for_a_french_session(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.id == session_id)
        .values(language=Language.FR)
    )

    cart = await add_item(
        db_session, session_id, "fries", selections={"size": ["large"]}, tax_rate=TAX
    )

    (line,) = cart.lines
    assert cart.language is Language.FR
    assert line.item_name == "Frites"
    assert [(o.group_name, o.option_name) for o in line.options] == [
        ("Format", "Grand"),
        ("Style", "Régulières"),
        ("Assaisonnement", "Nature"),
    ]
    # Codes never change with language.
    assert line.item_code == "fries"


async def test_names_fall_back_to_english_before_the_language_is_known(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    cart = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    assert cart.language is Language.EN
    assert cart.lines[0].item_name == "Chocolate chip cookie"


async def test_cart_prices_follow_the_menu(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """A cart stores no prices, so a menu change shows up at once."""
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(base_price_cents=175)
    )

    cart = await get_cart(db_session, session_id, tax_rate=TAX)
    assert cart.lines[0].unit_price_cents == 175


async def test_get_cart_does_not_change_anything(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    added = await add_item(db_session, session_id, "cookie", tax_rate=TAX)

    first = await get_cart(db_session, session_id, tax_rate=TAX)
    second = await get_cart(db_session, session_id, tax_rate=TAX)

    assert first == second == added


async def test_get_cart_for_an_unknown_session_is_reported(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(SessionNotFound):
        await get_cart(db_session, uuid.uuid4(), tax_rate=TAX)


# --- Session state ----------------------------------------------------------


async def test_editing_during_confirmation_returns_the_session_to_ordering(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await add_item(db_session, session_id, "cookie", tax_rate=TAX)
    await db_session.execute(
        update(CustomerSession)
        .where(CustomerSession.id == session_id)
        .values(state=SessionState.CONFIRMING)
    )

    cart = await update_line(db_session, session_id, 1, quantity=2, tax_rate=TAX)

    assert cart.state is SessionState.ORDERING


type Mutation = Callable[[AsyncSession, uuid.UUID], Awaitable[CartSnapshot]]

MUTATIONS: dict[str, Mutation] = {
    "add": lambda db, sid: add_item(db, sid, "cookie", tax_rate=TAX),
    "update": lambda db, sid: update_line(db, sid, 1, quantity=5, tax_rate=TAX),
    "remove": lambda db, sid: remove_line(db, sid, 1, tax_rate=TAX),
}


@pytest.mark.parametrize("mutation", MUTATIONS)
async def test_an_ended_session_refuses_every_change(
    db_session: AsyncSession, session_id: uuid.UUID, mutation: str
) -> None:
    before = await add_item(db_session, session_id, "cookie", tax_rate=TAX)
    await end_session(db_session, session_id, SessionState.ABANDONED, "left")

    with pytest.raises(SessionEnded):
        await MUTATIONS[mutation](db_session, session_id)

    after = await get_cart(db_session, session_id, tax_rate=TAX)
    assert after.lines == before.lines
    assert after.state is SessionState.ABANDONED
