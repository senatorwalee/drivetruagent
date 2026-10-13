"""The endpoints an agent uses: propose an action, then answer a held one.

Unlike the cart endpoints, everything here passes through the policy engine.
A proposal always gets 200 with the decision in the body, including when it
is rejected: a refusal is a normal answer to a proposal, not a failed request.
"""

import uuid

from fastapi import APIRouter

from drivetruagent.api.deps import IdempotencyKey, SessionDep, SettingsDep
from drivetruagent.api.errors import NOT_FOUND
from drivetruagent.policy import gateway
from drivetruagent.policy.actions import Action
from drivetruagent.policy.outcome import ActionOutcome

router = APIRouter(prefix="/sessions/{session_id}", tags=["agent actions"])


@router.post("/actions", responses=NOT_FOUND, summary="Propose an action")
async def propose_action(
    session_id: uuid.UUID,
    action: Action,
    db: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> ActionOutcome:
    """Propose an action and get the policy engine's decision.

    - `allow`: the action ran; `cart` is the new cart.
    - `reject`: nothing changed; `error` says why.
    - `confirm`: nothing changed yet; ask the customer, then confirm or cancel
      using `pending_action_id`.
    - `escalate`: the session has been handed to a person.
    """
    outcome = await gateway.execute(
        db,
        session_id,
        action,
        tax_rate=settings.tax_rate,
        limits=settings.policy_limits,
        reservation_ttl=settings.reservation_ttl,
        idempotency_key=idempotency_key,
    )
    await db.commit()
    return outcome


@router.post(
    "/pending-actions/{pending_action_id}/confirm",
    responses=NOT_FOUND,
    summary="The customer said yes",
)
async def confirm_pending_action(
    session_id: uuid.UUID,
    pending_action_id: uuid.UUID,
    db: SessionDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyKey = None,
) -> ActionOutcome:
    """Run the stored action, now that the customer has confirmed it."""
    outcome = await gateway.confirm(
        db,
        session_id,
        pending_action_id,
        tax_rate=settings.tax_rate,
        limits=settings.policy_limits,
        reservation_ttl=settings.reservation_ttl,
        idempotency_key=idempotency_key,
    )
    await db.commit()
    return outcome


@router.post(
    "/pending-actions/{pending_action_id}/cancel",
    responses=NOT_FOUND,
    summary="The customer said no",
)
async def cancel_pending_action(
    session_id: uuid.UUID,
    pending_action_id: uuid.UUID,
    db: SessionDep,
    settings: SettingsDep,
) -> ActionOutcome:
    outcome = await gateway.cancel(
        db, session_id, pending_action_id, tax_rate=settings.tax_rate
    )
    await db.commit()
    return outcome
