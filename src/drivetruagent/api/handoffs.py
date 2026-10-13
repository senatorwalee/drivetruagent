"""Handoff endpoints, for the person taking over a session.

There is no sign-in: this is a local demo. A real deployment would put these
behind staff authentication.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.api.deps import SessionDep, SettingsDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND
from drivetruagent.enums import HandoffOutcome, HandoffStatus
from drivetruagent.policy import handoffs
from drivetruagent.policy.handoff_package import HandoffPackage, HandoffSummary

router = APIRouter(prefix="/handoffs", tags=["handoffs"])


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Who has picked it up. Shown to anyone else looking at the queue.
    by: str = Field(default="staff", min_length=1, max_length=80)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: HandoffOutcome
    note: str | None = Field(default=None, max_length=500)


@router.get("", summary="The handoff queue")
async def read_queue(
    db: SessionDep,
    settings: SettingsDep,
    status: Annotated[
        HandoffStatus | None, Query(description="Only handoffs in this status.")
    ] = None,
    session_id: Annotated[
        uuid.UUID | None, Query(description="Only the handoff for this session.")
    ] = None,
) -> list[HandoffSummary]:
    """Sessions handed to a person, the longest-waiting first."""
    return await handoffs.list_handoffs(
        db, tax_rate=settings.tax_rate, status=status, session_id=session_id
    )


@router.get("/{handoff_id}", responses=NOT_FOUND, summary="One handoff's package")
async def read_package(
    handoff_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> HandoffPackage:
    """Everything the person taking over needs: why, what to do next, the
    cart, the stock it holds, and the recent conversation."""
    return await handoffs.get_package(
        db, handoff_id, tax_rate=settings.tax_rate, now=datetime.now(UTC)
    )


@router.post(
    "/{handoff_id}/acknowledge",
    responses=NOT_FOUND | CONFLICT,
    summary="Say you have picked a handoff up",
)
async def acknowledge(
    handoff_id: uuid.UUID,
    db: SessionDep,
    settings: SettingsDep,
    body: AcknowledgeRequest | None = None,
) -> HandoffPackage:
    """Doing it twice changes nothing and keeps the first person's name."""
    now = datetime.now(UTC)
    await handoffs.acknowledge(
        db, handoff_id, by=(body or AcknowledgeRequest()).by, now=now
    )
    await db.commit()
    return await handoffs.get_package(
        db, handoff_id, tax_rate=settings.tax_rate, now=now
    )


@router.post(
    "/{handoff_id}/resolve",
    responses=NOT_FOUND | CONFLICT,
    summary="Finish a handoff",
)
async def resolve(
    handoff_id: uuid.UUID, body: ResolveRequest, db: SessionDep, settings: SettingsDep
) -> HandoffPackage:
    """Place the order as it stands (`order_placed`) or drop it (`cancelled`).

    Placing it is refused if the cart is empty or its stock has gone; the
    handoff then stays as it was. A resolved handoff cannot be changed.
    """
    now = datetime.now(UTC)
    await handoffs.resolve(
        db,
        handoff_id,
        body.outcome,
        now=now,
        tax_rate=settings.tax_rate,
        note=body.note,
    )
    await db.commit()
    return await handoffs.get_package(
        db, handoff_id, tax_rate=settings.tax_rate, now=now
    )
