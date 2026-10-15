"""Opening and ending sessions against a real database."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.db.models import Cart, CustomerSession
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.errors import LaneBusy, SessionEnded, SessionNotFound
from drivetruagent.ordering.sessions import (
    end_session,
    follow_language,
    get_session,
    lock_session,
    open_session,
)


async def test_open_session_creates_a_session_with_an_empty_cart(
    db_session: AsyncSession,
) -> None:
    customer_session = await open_session(db_session, "lane-1")

    cart = (
        await db_session.execute(
            select(Cart).where(Cart.session_id == customer_session.id)
        )
    ).scalar_one()
    assert customer_session.state is SessionState.GREETING
    assert customer_session.lane == "lane-1"
    assert cart.version == 0


async def test_open_session_on_a_busy_lane_is_refused(
    db_session: AsyncSession,
) -> None:
    await open_session(db_session, "lane-1")

    with pytest.raises(LaneBusy) as error:
        await open_session(db_session, "lane-1")

    assert error.value.code == "lane_busy"
    assert error.value.details == {"lane": "lane-1"}


async def test_transaction_is_still_usable_after_a_busy_lane(
    db_session: AsyncSession,
) -> None:
    """The refused insert is undone on its own; earlier work survives."""
    first = await open_session(db_session, "lane-1")
    with pytest.raises(LaneBusy):
        await open_session(db_session, "lane-1")

    second = await open_session(db_session, "lane-2")

    count = (
        await db_session.execute(select(func.count()).select_from(CustomerSession))
    ).scalar_one()
    assert count == 2
    assert {first.lane, second.lane} == {"lane-1", "lane-2"}


async def test_lane_is_free_after_the_session_ends(db_session: AsyncSession) -> None:
    first = await open_session(db_session, "lane-1")
    await end_session(db_session, first.id, SessionState.ABANDONED, "silence_timeout")

    second = await open_session(db_session, "lane-1")

    assert second.id != first.id


async def test_end_session_records_the_outcome(db_session: AsyncSession) -> None:
    opened = await open_session(db_session)

    ended = await end_session(
        db_session, opened.id, SessionState.HANDED_OFF, "customer_asked_for_a_person"
    )

    assert ended.state is SessionState.HANDED_OFF
    assert ended.ended_at is not None
    assert ended.end_reason == "customer_asked_for_a_person"


async def test_an_ended_session_cannot_be_ended_again(
    db_session: AsyncSession,
) -> None:
    opened = await open_session(db_session)
    await end_session(db_session, opened.id, SessionState.ABANDONED, "left")

    with pytest.raises(SessionEnded) as error:
        await end_session(db_session, opened.id, SessionState.TERMINATED, "abuse")

    assert error.value.details == {"state": "abandoned"}


async def test_end_session_refuses_a_state_that_is_not_final(
    db_session: AsyncSession,
) -> None:
    opened = await open_session(db_session)

    with pytest.raises(ValueError, match="not a final state"):
        await end_session(db_session, opened.id, SessionState.ORDERING, "oops")


async def test_unknown_session_is_reported(db_session: AsyncSession) -> None:
    with pytest.raises(SessionNotFound) as error:
        await lock_session(db_session, uuid.uuid4())

    assert error.value.code == "session_not_found"


async def test_two_cars_racing_for_one_lane_cannot_both_get_it(
    committing_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Real concurrency: separate connections, both trying at the same moment."""

    async def try_to_open() -> str:
        try:
            async with committing_sessions.begin() as db:
                await open_session(db, "lane-1")
        except LaneBusy:
            return "busy"
        return "opened"

    results = await asyncio.gather(*(try_to_open() for _ in range(5)))

    assert sorted(results) == ["busy", "busy", "busy", "busy", "opened"]


# --- Following the customer's language --------------------------------------


async def test_the_session_follows_a_clear_language_and_ignores_unclear_words(
    db_session: AsyncSession,
) -> None:
    session_id = (await open_session(db_session, "lane-lang")).id

    assert await follow_language(db_session, session_id, "Bonjour, merci") is (
        Language.FR
    )
    # Too little to tell, or already French: nothing changes.
    assert await follow_language(db_session, session_id, "ok") is None
    assert await follow_language(db_session, session_id, "Oui merci") is None
    assert (await get_session(db_session, session_id)).language is Language.FR
    assert await follow_language(db_session, session_id, "Yes please") is Language.EN


async def test_an_ended_session_keeps_its_language(db_session: AsyncSession) -> None:
    session_id = (await open_session(db_session, "lane-lang")).id
    await end_session(db_session, session_id, SessionState.SUBMITTED, reason="done")

    assert await follow_language(db_session, session_id, "Bonjour, merci") is None
