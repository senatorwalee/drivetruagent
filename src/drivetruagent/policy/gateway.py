"""Where a proposed action becomes an effect, or does not.

This is the only code that turns a policy decision into something happening:

  allow     run the action through the ordering rules
  reject    change nothing
  confirm   store the action and wait for the customer's answer
  escalate  end the session as handed off to a person

Nothing here commits. The caller owns the transaction.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import PendingAction
from drivetruagent.enums import PendingActionStatus, SessionState
from drivetruagent.ordering import cart as cart_rules
from drivetruagent.ordering import orders as order_rules
from drivetruagent.ordering.errors import OrderingError, PendingActionNotFound
from drivetruagent.ordering.sessions import end_session, lock_session
from drivetruagent.ordering.views import CartSnapshot, OrderView
from drivetruagent.policy.actions import (
    ACTION_ADAPTER,
    Action,
    AddItem,
    RemoveLine,
    RequestConfirmation,
    SubmitOrder,
    UpdateLine,
)
from drivetruagent.policy.decisions import (
    Decision,
    PolicyContext,
    PolicyLimits,
    PolicyResult,
)
from drivetruagent.policy.outcome import ActionOutcome, OutcomeError
from drivetruagent.policy.rules import evaluate

PENDING_ACTION_RESOLVED = "pending_action_resolved"
PENDING_ACTION_STALE = "pending_action_stale"
PENDING_ACTION_CANCELLED = "pending_action_cancelled"


async def execute(
    db: AsyncSession,
    session_id: uuid.UUID,
    action: Action,
    *,
    tax_rate: Decimal,
    limits: PolicyLimits,
) -> ActionOutcome:
    """Evaluate a proposed action and apply the decision."""
    # The lock is held until the caller's transaction ends, so the decision
    # and its effect cannot be separated by another change to this session.
    await lock_session(db, session_id)
    snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)
    result = evaluate(action, _context(snapshot), limits)

    if result.decision is Decision.ALLOW:
        return await _run(db, session_id, action, tax_rate)
    if result.decision is Decision.CONFIRM:
        pending = await _hold_for_confirmation(db, session_id, action, result, snapshot)
        return ActionOutcome(
            decision=Decision.CONFIRM,
            rule=result.rule,
            message=result.message,
            cart=snapshot,
            pending_action_id=pending.id,
        )
    if result.decision is Decision.ESCALATE:
        return await _escalate(db, session_id, result, tax_rate)
    return _rejected(result, snapshot)


async def confirm(
    db: AsyncSession,
    session_id: uuid.UUID,
    pending_action_id: uuid.UUID,
    *,
    tax_rate: Decimal,
    limits: PolicyLimits,
) -> ActionOutcome:
    """The customer said yes to a held action.

    What runs is the stored copy of the action, evaluated again as confirmed.
    """
    await lock_session(db, session_id)
    pending = await _load_pending(db, session_id, pending_action_id)
    snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)

    not_usable = await _why_not_usable(db, pending, snapshot)
    if not_usable is not None:
        return _rejected(not_usable, snapshot)

    action = ACTION_ADAPTER.validate_python(pending.action)
    result = evaluate(action, _context(snapshot), limits, confirmed=True)

    if result.decision is Decision.ESCALATE:
        _resolve(pending, PendingActionStatus.ESCALATED)
        return await _escalate(db, session_id, result, tax_rate)
    if result.decision is not Decision.ALLOW:
        _resolve(pending, PendingActionStatus.CANCELLED)
        await db.flush()
        return _rejected(result, snapshot)

    _resolve(pending, PendingActionStatus.CONFIRMED)
    await db.flush()
    outcome = await _run(db, session_id, action, tax_rate)
    if outcome.decision is Decision.REJECT:
        # Confirmed, but it could not run after all (the item became
        # unavailable, say). Record that it did not happen.
        pending.status = PendingActionStatus.CANCELLED
        await db.flush()
    return outcome


async def cancel(
    db: AsyncSession,
    session_id: uuid.UUID,
    pending_action_id: uuid.UUID,
    *,
    tax_rate: Decimal,
) -> ActionOutcome:
    """The customer said no to a held action. Nothing changes."""
    await lock_session(db, session_id)
    pending = await _load_pending(db, session_id, pending_action_id)
    snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)

    not_usable = await _why_not_usable(db, pending, snapshot)
    if not_usable is not None and not_usable.rule == PENDING_ACTION_RESOLVED:
        return _rejected(not_usable, snapshot)

    if pending.status is PendingActionStatus.PENDING:
        _resolve(pending, PendingActionStatus.CANCELLED)
        await db.flush()
    return ActionOutcome(
        decision=Decision.ALLOW,
        rule=PENDING_ACTION_CANCELLED,
        message="Cancelled. Nothing was changed. Ask what the customer would like.",
        cart=snapshot,
    )


# --- Applying a decision ----------------------------------------------------


async def _run(
    db: AsyncSession, session_id: uuid.UUID, action: Action, tax_rate: Decimal
) -> ActionOutcome:
    """Run an allowed action. An ordering error becomes a rejection."""
    try:
        # A savepoint: if the ordering rules refuse, anything they had started
        # is undone, and the caller's transaction carries on.
        async with db.begin_nested():
            snapshot, order = await _perform(db, session_id, action, tax_rate)
    except OrderingError as error:
        snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)
        return ActionOutcome(
            decision=Decision.REJECT,
            rule=error.code,
            message=error.message,
            cart=snapshot,
            error=OutcomeError(
                code=error.code, message=error.message, details=error.details
            ),
        )

    # Something happened, so a question still waiting for an answer is stale.
    await _supersede_pending(db, session_id)
    return ActionOutcome(
        decision=Decision.ALLOW,
        message=_done_message(action),
        cart=snapshot,
        order=order,
    )


async def _perform(
    db: AsyncSession, session_id: uuid.UUID, action: Action, tax_rate: Decimal
) -> tuple[CartSnapshot, OrderView | None]:
    if isinstance(action, AddItem):
        snapshot = await cart_rules.add_item(
            db,
            session_id,
            action.item_code,
            action.quantity,
            action.selections,
            tax_rate=tax_rate,
        )
        return snapshot, None
    if isinstance(action, UpdateLine):
        snapshot = await cart_rules.update_line(
            db,
            session_id,
            action.line_no,
            action.quantity,
            action.selections,
            tax_rate=tax_rate,
        )
        return snapshot, None
    if isinstance(action, RemoveLine):
        snapshot = await cart_rules.remove_line(
            db, session_id, action.line_no, tax_rate=tax_rate
        )
        return snapshot, None
    if isinstance(action, RequestConfirmation):
        snapshot = await cart_rules.request_confirmation(
            db, session_id, tax_rate=tax_rate
        )
        return snapshot, None

    order = await order_rules.submit_order(db, session_id, tax_rate=tax_rate)
    snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)
    return snapshot, order


def _done_message(action: Action) -> str:
    if isinstance(action, RequestConfirmation):
        return (
            "Read the order and the total back to the customer and ask if it is right."
        )
    if isinstance(action, SubmitOrder):
        return "The order is placed. Give the customer their order number and total."
    return "Done. Briefly tell the customer what changed."


async def _hold_for_confirmation(
    db: AsyncSession,
    session_id: uuid.UUID,
    action: Action,
    result: PolicyResult,
    snapshot: CartSnapshot,
) -> PendingAction:
    # A new question replaces any earlier one that was never answered.
    await _supersede_pending(db, session_id)
    pending = PendingAction(
        session_id=session_id,
        action=action.model_dump(mode="json"),
        rule=result.rule or "",
        message=result.message,
        cart_version=snapshot.version,
    )
    db.add(pending)
    await db.flush()
    return pending


async def _escalate(
    db: AsyncSession, session_id: uuid.UUID, result: PolicyResult, tax_rate: Decimal
) -> ActionOutcome:
    await _supersede_pending(db, session_id)
    await end_session(
        db, session_id, SessionState.HANDED_OFF, reason=result.rule or "escalated"
    )
    snapshot = await cart_rules.get_cart(db, session_id, tax_rate=tax_rate)
    return ActionOutcome(
        decision=Decision.ESCALATE,
        rule=result.rule,
        message=result.message,
        cart=snapshot,
    )


def _rejected(result: PolicyResult, snapshot: CartSnapshot) -> ActionOutcome:
    code = result.rule or "rejected"
    return ActionOutcome(
        decision=Decision.REJECT,
        rule=code,
        message=result.message,
        cart=snapshot,
        error=OutcomeError(
            code=code, message=result.message, details=dict(result.details)
        ),
    )


# --- Pending actions --------------------------------------------------------


async def _load_pending(
    db: AsyncSession, session_id: uuid.UUID, pending_action_id: uuid.UUID
) -> PendingAction:
    statement = (
        select(PendingAction)
        # Matching on the session as well means one session can never answer
        # a question that was put to another.
        .where(
            PendingAction.id == pending_action_id,
            PendingAction.session_id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    pending = (await db.execute(statement)).scalar_one_or_none()
    if pending is None:
        raise PendingActionNotFound(pending_action_id)
    return pending


async def _why_not_usable(
    db: AsyncSession, pending: PendingAction, snapshot: CartSnapshot
) -> PolicyResult | None:
    """A rejection if this pending action can no longer be answered."""
    if pending.status is not PendingActionStatus.PENDING:
        return PolicyResult(
            Decision.REJECT,
            PENDING_ACTION_RESOLVED,
            "That question has already been dealt with. Ask the customer what "
            "they would like.",
            {"status": pending.status.value},
        )
    if pending.cart_version != snapshot.version:
        _resolve(pending, PendingActionStatus.SUPERSEDED)
        await db.flush()
        return PolicyResult(
            Decision.REJECT,
            PENDING_ACTION_STALE,
            "The cart has changed since that question was asked. Propose the "
            "action again if the customer still wants it.",
        )
    return None


def _resolve(pending: PendingAction, status: PendingActionStatus) -> None:
    pending.status = status
    pending.resolved_at = datetime.now(UTC)


async def _supersede_pending(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(PendingAction)
        .where(
            PendingAction.session_id == session_id,
            PendingAction.status == PendingActionStatus.PENDING,
        )
        .values(
            status=PendingActionStatus.SUPERSEDED,
            resolved_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )


def _context(snapshot: CartSnapshot) -> PolicyContext:
    return PolicyContext(
        state=snapshot.state,
        cart_item_count=snapshot.item_count,
        line_quantities={line.line_no: line.quantity for line in snapshot.lines},
    )
