"""The policy gateway against a real database and the real menu.

The rules themselves are covered in tests/unit/test_policy_rules.py. These
tests check that each decision has the right effect, and no other.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import MenuItem, Order, PendingAction
from drivetruagent.enums import PendingActionStatus, SessionState
from drivetruagent.ordering.cart import add_item
from drivetruagent.ordering.errors import PendingActionNotFound, SessionNotFound
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import (
    Action,
    AddItem,
    RemoveLine,
    RequestConfirmation,
    SubmitOrder,
    UpdateLine,
)
from drivetruagent.policy.decisions import Decision, PolicyLimits
from drivetruagent.policy.outcome import ActionOutcome

TAX = Decimal("0.13")
LIMITS = PolicyLimits(
    line_quantity_confirm_above=10,
    line_quantity_escalate_above=25,
    cart_items_escalate_above=50,
)


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    return (await open_session(db_session)).id


async def propose(
    db: AsyncSession, session_id: uuid.UUID, action: Action
) -> ActionOutcome:
    return await gateway.execute(db, session_id, action, tax_rate=TAX, limits=LIMITS)


async def say_yes(
    db: AsyncSession, session_id: uuid.UUID, outcome: ActionOutcome
) -> ActionOutcome:
    assert outcome.pending_action_id is not None
    return await gateway.confirm(
        db, session_id, outcome.pending_action_id, tax_rate=TAX, limits=LIMITS
    )


async def say_no(
    db: AsyncSession, session_id: uuid.UUID, outcome: ActionOutcome
) -> ActionOutcome:
    assert outcome.pending_action_id is not None
    return await gateway.cancel(db, session_id, outcome.pending_action_id, tax_rate=TAX)


async def pending_status(
    db: AsyncSession, outcome: ActionOutcome
) -> PendingActionStatus:
    assert outcome.pending_action_id is not None
    pending = await db.get_one(
        PendingAction, outcome.pending_action_id, populate_existing=True
    )
    return pending.status


FIFTEEN_COOKIES = AddItem(item_code="cookie", quantity=15)
THOUSAND_FRIES = AddItem(
    item_code="fries", quantity=1000, selections={"size": ["large"]}
)


# --- Allow ------------------------------------------------------------------


async def test_an_ordinary_add_runs_and_returns_the_new_cart(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    outcome = await propose(
        db_session,
        session_id,
        AddItem(item_code="fries", quantity=2, selections={"size": ["large"]}),
    )

    assert outcome.decision is Decision.ALLOW
    assert outcome.rule is None
    assert outcome.error is None
    assert outcome.pending_action_id is None
    assert outcome.cart.item_count == 2
    assert outcome.cart.subtotal_cents == 758


async def test_update_and_remove_run_through_the_gateway(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie"))
    await propose(db_session, session_id, AddItem(item_code="apple_pie"))

    updated = await propose(db_session, session_id, UpdateLine(line_no=1, quantity=4))
    removed = await propose(db_session, session_id, RemoveLine(line_no=2))

    assert updated.decision is removed.decision is Decision.ALLOW
    assert [(line.line_no, line.quantity) for line in removed.cart.lines] == [(1, 4)]


# --- Reject -----------------------------------------------------------------


async def test_an_ordering_error_comes_back_as_a_rejection(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    outcome = await propose(db_session, session_id, AddItem(item_code="fries"))

    assert outcome.decision is Decision.REJECT
    assert outcome.rule == "missing_required_options"
    assert outcome.error is not None
    assert outcome.error.code == "missing_required_options"
    missing = outcome.error.details["missing"]
    assert [group["group_code"] for group in missing] == ["size"]
    assert outcome.cart.lines == []
    assert outcome.cart.version == 0


@pytest.mark.parametrize(
    ("action", "code"),
    [
        (AddItem(item_code="lobster"), "unknown_item"),
        (AddItem(item_code="cookie", quantity=0), "invalid_quantity"),
        (UpdateLine(line_no=9, quantity=2), "unknown_cart_line"),
        (RemoveLine(line_no=9), "unknown_cart_line"),
        (UpdateLine(line_no=1), "nothing_to_update"),
        (RequestConfirmation(), "empty_cart"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
async def test_rejections_carry_the_ordering_error_code(
    db_session: AsyncSession, session_id: uuid.UUID, action: Action, code: str
) -> None:
    outcome = await propose(db_session, session_id, action)

    assert outcome.decision is Decision.REJECT
    assert outcome.rule == code
    assert outcome.cart.lines == []


async def test_a_rejection_leaves_earlier_work_in_place(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    before = await propose(db_session, session_id, AddItem(item_code="cookie"))

    rejected = await propose(db_session, session_id, AddItem(item_code="lobster"))

    assert rejected.cart == before.cart


async def test_unknown_session_is_an_error_not_a_rejection(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(SessionNotFound):
        await propose(db_session, uuid.uuid4(), AddItem(item_code="cookie"))


# --- Read-back before submitting --------------------------------------------


async def test_submit_is_refused_until_the_order_has_been_read_back(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie"))

    outcome = await propose(db_session, session_id, SubmitOrder())

    assert outcome.decision is Decision.REJECT
    assert outcome.rule == "order_not_confirmed"
    assert outcome.order is None
    orders = (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar_one()
    assert orders == 0


async def test_read_back_then_submit_places_the_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie", quantity=2))

    read_back = await propose(db_session, session_id, RequestConfirmation())
    submitted = await propose(db_session, session_id, SubmitOrder())

    assert read_back.decision is Decision.ALLOW
    assert read_back.cart.state is SessionState.CONFIRMING
    # Reading the order back does not change it.
    assert read_back.cart.version == 1
    assert submitted.decision is Decision.ALLOW
    assert submitted.order is not None
    assert submitted.order.total_cents == 337  # 298 + 13% = 336.74
    assert submitted.cart.state is SessionState.SUBMITTED


async def test_a_change_after_read_back_needs_a_new_read_back(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie"))
    await propose(db_session, session_id, RequestConfirmation())

    changed = await propose(db_session, session_id, AddItem(item_code="apple_pie"))
    too_soon = await propose(db_session, session_id, SubmitOrder())
    await propose(db_session, session_id, RequestConfirmation())
    submitted = await propose(db_session, session_id, SubmitOrder())

    assert changed.cart.state is SessionState.ORDERING
    assert too_soon.rule == "order_not_confirmed"
    assert submitted.decision is Decision.ALLOW
    assert submitted.order is not None
    assert submitted.order.item_count == 2


async def test_nothing_is_allowed_after_the_order_is_placed(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie"))
    await propose(db_session, session_id, RequestConfirmation())
    await propose(db_session, session_id, SubmitOrder())

    for action in (AddItem(item_code="cookie"), SubmitOrder(), RequestConfirmation()):
        outcome = await propose(db_session, session_id, action)
        assert (outcome.decision, outcome.rule) == (Decision.REJECT, "session_ended")


# --- Confirm ----------------------------------------------------------------


async def test_unusual_quantity_is_held_and_nothing_is_added(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    outcome = await propose(db_session, session_id, FIFTEEN_COOKIES)

    assert outcome.decision is Decision.CONFIRM
    assert outcome.rule == "unusual_quantity"
    assert outcome.pending_action_id is not None
    assert "15" in outcome.message
    assert outcome.cart.lines == []
    assert await pending_status(db_session, outcome) is PendingActionStatus.PENDING


async def test_saying_yes_runs_the_stored_action(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)

    outcome = await say_yes(db_session, session_id, held)

    assert outcome.decision is Decision.ALLOW
    assert [(line.item_code, line.quantity) for line in outcome.cart.lines] == [
        ("cookie", 15)
    ]
    assert await pending_status(db_session, held) is PendingActionStatus.CONFIRMED


async def test_saying_no_changes_nothing(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)

    outcome = await say_no(db_session, session_id, held)

    assert outcome.decision is Decision.ALLOW
    assert outcome.rule == "pending_action_cancelled"
    assert outcome.cart.lines == []
    assert await pending_status(db_session, held) is PendingActionStatus.CANCELLED


async def test_an_answered_question_cannot_be_answered_again(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    await say_yes(db_session, session_id, held)

    again = await say_yes(db_session, session_id, held)

    assert (again.decision, again.rule) == (Decision.REJECT, "pending_action_resolved")
    assert again.error is not None
    assert again.error.details == {"status": "confirmed"}
    # Still 15, not 30.
    assert again.cart.item_count == 15


async def test_a_cancelled_question_cannot_be_confirmed(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    await say_no(db_session, session_id, held)

    outcome = await say_yes(db_session, session_id, held)

    assert outcome.rule == "pending_action_resolved"
    assert outcome.cart.lines == []


async def test_a_question_goes_stale_when_the_cart_changes(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "Yes" must refer to the question just asked."""
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    await propose(db_session, session_id, AddItem(item_code="apple_pie"))

    outcome = await say_yes(db_session, session_id, held)

    assert outcome.decision is Decision.REJECT
    assert outcome.rule == "pending_action_resolved"
    assert [line.item_code for line in outcome.cart.lines] == ["apple_pie"]
    assert await pending_status(db_session, held) is PendingActionStatus.SUPERSEDED


async def test_a_question_goes_stale_when_staff_change_the_cart_directly(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """The staff endpoints change the cart without going through the gateway.
    The cart version recorded with the question catches that."""
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    await add_item(db_session, session_id, "apple_pie", tax_rate=TAX)

    outcome = await say_yes(db_session, session_id, held)

    assert (outcome.decision, outcome.rule) == (Decision.REJECT, "pending_action_stale")
    assert [line.item_code for line in outcome.cart.lines] == ["apple_pie"]
    assert await pending_status(db_session, held) is PendingActionStatus.SUPERSEDED


async def test_a_new_question_replaces_an_unanswered_one(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    first = await propose(db_session, session_id, FIFTEEN_COOKIES)
    second = await propose(
        db_session, session_id, AddItem(item_code="apple_pie", quantity=12)
    )

    answer_to_first = await say_yes(db_session, session_id, first)
    answer_to_second = await say_yes(db_session, session_id, second)

    assert answer_to_first.rule == "pending_action_resolved"
    assert answer_to_second.decision is Decision.ALLOW
    assert [
        (line.item_code, line.quantity) for line in answer_to_second.cart.lines
    ] == [("apple_pie", 12)]
    pending_count = (
        await db_session.execute(
            select(func.count())
            .select_from(PendingAction)
            .where(PendingAction.status == PendingActionStatus.PENDING)
        )
    ).scalar_one()
    assert pending_count == 0


async def test_an_unusual_update_is_held_and_then_applied(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(db_session, session_id, AddItem(item_code="cookie", quantity=2))

    held = await propose(db_session, session_id, UpdateLine(line_no=1, quantity=20))
    unchanged = held.cart.lines[0].quantity
    applied = await say_yes(db_session, session_id, held)

    assert held.decision is Decision.CONFIRM
    assert unchanged == 2
    assert applied.cart.lines[0].quantity == 20


async def test_a_confirmed_action_that_can_no_longer_run_is_rejected(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(is_active=False)
    )

    outcome = await say_yes(db_session, session_id, held)

    assert (outcome.decision, outcome.rule) == (Decision.REJECT, "item_unavailable")
    assert outcome.cart.lines == []
    assert await pending_status(db_session, held) is PendingActionStatus.CANCELLED


async def test_another_session_cannot_answer_this_sessions_question(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, FIFTEEN_COOKIES)
    other_session = (await open_session(db_session, "lane-2")).id

    with pytest.raises(PendingActionNotFound):
        await say_yes(db_session, other_session, held)

    assert await pending_status(db_session, held) is PendingActionStatus.PENDING


async def test_unknown_pending_action_is_an_error(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(PendingActionNotFound):
        await gateway.confirm(
            db_session, session_id, uuid.uuid4(), tax_rate=TAX, limits=LIMITS
        )


# --- Escalate ---------------------------------------------------------------


async def test_a_thousand_fries_is_asked_about_then_handed_to_a_person(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await propose(
        db_session,
        session_id,
        AddItem(item_code="cola", selections={"size": ["small"]}),
    )

    held = await propose(db_session, session_id, THOUSAND_FRIES)
    outcome = await say_yes(db_session, session_id, held)

    assert (held.decision, held.rule) == (Decision.CONFIRM, "extreme_quantity")
    assert outcome.decision is Decision.ESCALATE
    assert outcome.rule == "extreme_quantity"
    assert outcome.cart.state is SessionState.HANDED_OFF
    # The fries were never added; the cart the person takes over is intact.
    assert [line.item_code for line in outcome.cart.lines] == ["cola"]
    assert await pending_status(db_session, held) is PendingActionStatus.ESCALATED


async def test_declining_a_thousand_fries_keeps_the_session_going(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "No, I said ten fries." """
    held = await propose(db_session, session_id, THOUSAND_FRIES)
    await say_no(db_session, session_id, held)

    outcome = await propose(
        db_session,
        session_id,
        AddItem(item_code="fries", quantity=10, selections={"size": ["large"]}),
    )

    assert outcome.decision is Decision.ALLOW
    assert outcome.cart.state is SessionState.ORDERING
    assert outcome.cart.item_count == 10


async def test_the_lane_is_free_after_a_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    held = await propose(db_session, session_id, THOUSAND_FRIES)
    await say_yes(db_session, session_id, held)

    next_car = await open_session(db_session)

    assert next_car.id != session_id


async def test_a_cart_that_grows_too_large_is_escalated(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    for _ in range(5):
        await propose(db_session, session_id, AddItem(item_code="cookie", quantity=10))

    held = await propose(db_session, session_id, AddItem(item_code="apple_pie"))
    outcome = await say_yes(db_session, session_id, held)

    assert held.decision is Decision.CONFIRM
    assert outcome.decision is Decision.ESCALATE
    assert outcome.cart.item_count == 50
