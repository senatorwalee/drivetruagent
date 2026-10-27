"""Phone orders waiting to be collected, for staff at the counter.

    GET  /pickups              waiting orders, then the recently marked ones
    POST /pickups/{order_id}   mark one picked up, or a no-show

There is no sign-in: this is a local demo. A real deployment would put these
behind staff authentication.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.api.deps import SessionDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND
from drivetruagent.enums import PickupStatus
from drivetruagent.ordering import pickups
from drivetruagent.ordering.pickups import Pickup

router = APIRouter(prefix="/pickups", tags=["pickups"])


class MarkPickupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[PickupStatus.PICKED_UP, PickupStatus.NO_SHOW]
    # Who marked it, kept with the mark.
    by: str = Field(default="staff", min_length=1, max_length=80)


@router.get("", summary="Phone orders to be collected")
async def read_pickups(db: SessionDep) -> list[Pickup]:
    """Every phone order still waiting, oldest first; then those marked in
    the last twelve hours, so a wrong mark can be put right."""
    return await pickups.list_pickups(db, now=datetime.now(UTC))


@router.post(
    "/{order_id}",
    responses=NOT_FOUND | CONFLICT,
    summary="Mark a phone order picked up, or a no-show",
)
async def mark_pickup(
    order_id: uuid.UUID, body: MarkPickupRequest, db: SessionDep
) -> Pickup:
    """Only staff mark no-shows; two within 90 days flag the number, and big
    orders from it then go to a person. A mark can be changed."""
    pickup = await pickups.mark_pickup(
        db, order_id, body.status, by=body.by, now=datetime.now(UTC)
    )
    await db.commit()
    return pickup
