"""The database must refuse invalid sessions, carts and cart lines.

Each rejection test names the constraint it expects, so a test cannot pass
because some other rule happened to fire.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import (
    Cart,
    CartLine,
    CartLineOption,
    CustomerSession,
    MenuItem,
    Option,
)
from drivetruagent.enums import (
    ACTIVE_SESSION_STATES,
    FINAL_SESSION_STATES,
    Language,
    SessionState,
)
from tests.integration.factories import (
    make_cart,
    make_cart_line,
    make_group,
    make_item,
    make_option,
    make_session,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


async def count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- Sessions ---------------------------------------------------------------


async def test_new_session_starts_at_greeting(db_session: AsyncSession) -> None:
    customer_session = await make_session(db_session)

    assert customer_session.state is SessionState.GREETING
    assert customer_session.language is None
    assert customer_session.started_at is not None
    assert customer_session.ended_at is None


async def test_session_lane_defaults_to_lane_1(db_session: AsyncSession) -> None:
    customer_session = CustomerSession(business_date=date(2026, 10, 12))
    db_session.add(customer_session)
    await db_session.flush()

    assert customer_session.lane == "lane-1"


async def test_a_session_must_have_a_business_date(db_session: AsyncSession) -> None:
    """No database default: a date guessed in the wrong timezone would hand
    out the wrong day's specials around midnight."""
    db_session.add(CustomerSession())

    with pytest.raises(IntegrityError, match="business_date"):
        await db_session.flush()


def test_every_state_is_either_active_or_final() -> None:
    assert set(SessionState) == ACTIVE_SESSION_STATES | FINAL_SESSION_STATES
    assert not ACTIVE_SESSION_STATES & FINAL_SESSION_STATES


async def test_two_active_sessions_in_one_lane_are_rejected(
    db_session: AsyncSession,
) -> None:
    await make_session(db_session, lane="lane-1")

    with pytest.raises(IntegrityError, match="uq_sessions_one_active_per_lane"):
        await make_session(db_session, lane="lane-1")


@pytest.mark.parametrize("first_state", sorted(ACTIVE_SESSION_STATES))
async def test_lane_is_busy_in_every_active_state(
    db_session: AsyncSession, first_state: SessionState
) -> None:
    await make_session(db_session, lane="lane-1", state=first_state)

    with pytest.raises(IntegrityError, match="uq_sessions_one_active_per_lane"):
        await make_session(db_session, lane="lane-1")


@pytest.mark.parametrize("final_state", sorted(FINAL_SESSION_STATES))
async def test_lane_is_free_again_after_a_session_ends(
    db_session: AsyncSession, final_state: SessionState
) -> None:
    await make_session(db_session, lane="lane-1", state=final_state, ended_at=NOW)

    await make_session(db_session, lane="lane-1")

    assert await count(db_session, CustomerSession) == 2


async def test_active_sessions_in_two_lanes_are_allowed(
    db_session: AsyncSession,
) -> None:
    await make_session(db_session, lane="lane-1")
    await make_session(db_session, lane="lane-2")

    assert await count(db_session, CustomerSession) == 2


async def test_final_state_without_ended_at_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_sessions_ended_at_matches_state"):
        await make_session(db_session, state=SessionState.SUBMITTED)


async def test_active_state_with_ended_at_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_sessions_ended_at_matches_state"):
        await make_session(db_session, state=SessionState.ORDERING, ended_at=NOW)


async def test_ending_a_session_requires_setting_ended_at(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)
    customer_session.state = SessionState.ABANDONED

    with pytest.raises(IntegrityError, match="ck_sessions_ended_at_matches_state"):
        await db_session.flush()


async def test_unknown_state_is_rejected(db_session: AsyncSession) -> None:
    # Raw SQL: the ORM would refuse the value in Python before the database.
    # ended_at is set so that only the state check can object to this row.
    with pytest.raises(IntegrityError, match="ck_sessions_state_valid"):
        await db_session.execute(
            text(
                "INSERT INTO sessions (lane, state, ended_at, business_date) "
                "VALUES ('lane-9', 'paused', now(), '2026-10-12')"
            )
        )


async def test_unknown_language_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_sessions_language_valid"):
        await db_session.execute(
            text(
                "INSERT INTO sessions (lane, language, business_date) "
                "VALUES ('lane-9', 'de', '2026-10-12')"
            )
        )


async def test_session_state_and_language_load_as_enums(
    db_session: AsyncSession,
) -> None:
    created = await make_session(
        db_session, state=SessionState.ORDERING, language=Language.FR
    )
    db_session.expunge_all()

    loaded = await db_session.get_one(CustomerSession, created.id)

    assert loaded.state is SessionState.ORDERING
    assert loaded.language is Language.FR


# --- Carts ------------------------------------------------------------------


async def test_new_cart_starts_at_version_0_and_line_1(
    db_session: AsyncSession,
) -> None:
    cart = await make_cart(db_session)

    assert cart.version == 0
    assert cart.next_line_no == 1


async def test_second_cart_for_one_session_is_rejected(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)
    await make_cart(db_session, customer_session)

    with pytest.raises(IntegrityError, match="uq_carts_session_id"):
        await make_cart(db_session, customer_session)


async def test_negative_cart_version_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_carts_version_non_negative"):
        await make_cart(db_session, version=-1)


async def test_next_line_no_below_1_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_carts_next_line_no_positive"):
        await make_cart(db_session, next_line_no=0)


# --- Cart lines -------------------------------------------------------------


async def test_cart_line_with_quantity_0_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_cart_lines_quantity_positive"):
        await make_cart_line(db_session, quantity=0)


async def test_cart_line_number_below_1_is_rejected(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError, match="ck_cart_lines_line_no_positive"):
        await make_cart_line(db_session, line_no=0)


async def test_two_lines_with_the_same_number_in_one_cart_are_rejected(
    db_session: AsyncSession,
) -> None:
    cart = await make_cart(db_session)
    await make_cart_line(db_session, cart, line_no=1)

    with pytest.raises(IntegrityError, match="uq_cart_lines_cart_id_line_no"):
        await make_cart_line(db_session, cart, line_no=1)


async def test_same_line_number_in_two_carts_is_allowed(
    db_session: AsyncSession,
) -> None:
    await make_cart_line(db_session, line_no=1)
    await make_cart_line(db_session, line_no=1)

    assert await count(db_session, CartLine) == 2


async def test_same_item_on_two_lines_is_allowed(db_session: AsyncSession) -> None:
    """Two fries with different options are two lines of the same item."""
    cart = await make_cart(db_session)
    item = await make_item(db_session)
    await make_cart_line(db_session, cart, item, line_no=1)
    await make_cart_line(db_session, cart, item, line_no=2)

    assert await count(db_session, CartLine) == 2


async def test_menu_item_in_a_cart_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    item = await make_item(db_session)
    await make_cart_line(db_session, item=item)

    with pytest.raises(IntegrityError, match="fk_cart_lines_item_id_menu_items"):
        await db_session.execute(delete(MenuItem).where(MenuItem.id == item.id))


# --- Chosen options ---------------------------------------------------------


async def test_same_option_twice_on_one_line_is_rejected(
    db_session: AsyncSession,
) -> None:
    line = await make_cart_line(db_session)
    option = await make_option(db_session)
    choice = {"cart_line_id": line.id, "option_id": option.id}
    await db_session.execute(
        text(
            "INSERT INTO cart_line_options (cart_line_id, option_id) "
            "VALUES (:cart_line_id, :option_id)"
        ),
        choice,
    )

    with pytest.raises(IntegrityError, match="pk_cart_line_options"):
        await db_session.execute(
            text(
                "INSERT INTO cart_line_options (cart_line_id, option_id) "
                "VALUES (:cart_line_id, :option_id)"
            ),
            choice,
        )


async def test_option_chosen_in_a_cart_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    line = await make_cart_line(db_session)
    option = await make_option(db_session)
    db_session.add(CartLineOption(cart_line_id=line.id, option_id=option.id))
    await db_session.flush()

    with pytest.raises(IntegrityError, match="fk_cart_line_options_option_id_options"):
        await db_session.execute(delete(Option).where(Option.id == option.id))


# --- Cascades ---------------------------------------------------------------


async def cart_with_one_line_and_option(session: AsyncSession) -> Cart:
    item = await make_item(session)
    option = await make_option(session, await make_group(session, item))
    cart = await make_cart(session)
    line = await make_cart_line(session, cart, item)
    session.add(CartLineOption(cart_line_id=line.id, option_id=option.id))
    await session.flush()
    return cart


async def test_deleting_a_cart_removes_its_lines_and_their_options(
    db_session: AsyncSession,
) -> None:
    cart = await cart_with_one_line_and_option(db_session)

    await db_session.execute(delete(Cart).where(Cart.id == cart.id))

    assert await count(db_session, CartLine) == 0
    assert await count(db_session, CartLineOption) == 0
    # The menu is untouched.
    assert await count(db_session, Option) == 1


async def test_deleting_a_session_removes_its_cart(db_session: AsyncSession) -> None:
    cart = await cart_with_one_line_and_option(db_session)

    await db_session.execute(
        delete(CustomerSession).where(CustomerSession.id == cart.session_id)
    )

    assert await count(db_session, Cart) == 0
    assert await count(db_session, CartLine) == 0
