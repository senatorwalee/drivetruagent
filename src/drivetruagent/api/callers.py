"""Phone numbers that have called, for staff.

    GET    /callers                     every number, the latest call first
    POST   /callers/{caller_id}/status  flag, block or clear a number
    DELETE /callers/{caller_id}         forget a number, as a caller may ask

There is no sign-in: this is a local demo. A real deployment would put these
behind staff authentication; the whole number is shown only to staff.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.api.deps import SessionDep, SettingsDep
from drivetruagent.api.errors import NOT_FOUND
from drivetruagent.config import Settings
from drivetruagent.enums import CallerStatus
from drivetruagent.ordering import callers
from drivetruagent.ordering.callers import CallerSummary
from drivetruagent.policy.caller_rules import Standing, standing
from drivetruagent.policy.callers import counts_of

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callers", tags=["callers"])


class CallerView(CallerSummary):
    # What the counts and staff's decision add up to now.
    standing: Standing


class SetStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CallerStatus
    # Required: every change is recorded with why it was made.
    reason: str = Field(min_length=3, max_length=500)
    by: str = Field(default="staff", min_length=1, max_length=80)


async def _callers(
    db: AsyncSession, settings: Settings, caller_id: uuid.UUID | None = None
) -> list[CallerView]:
    limits = settings.caller_limits
    summaries = await callers.caller_summaries(
        db,
        now=datetime.now(UTC),
        no_show_window=limits.no_show_window,
        repeat_call_window=limits.repeat_call_window,
        caller_id=caller_id,
    )
    return [
        CallerView(
            **summary.model_dump(), standing=standing(counts_of(summary), limits)
        )
        for summary in summaries
    ]


@router.get("", summary="Phone numbers that have called")
async def read_callers(db: SessionDep, settings: SettingsDep) -> list[CallerView]:
    """The latest call first, with no-shows in the window, calls without an
    order in the repeat-call window, and each number's standing."""
    return await _callers(db, settings)


@router.post(
    "/{caller_id}/status",
    responses=NOT_FOUND,
    summary="Flag, block or clear a number",
)
async def set_status(
    caller_id: uuid.UUID, body: SetStatusRequest, db: SessionDep, settings: SettingsDep
) -> CallerView:
    """Recorded with who did it and why. Setting the status a number already
    has changes nothing and records nothing."""
    await callers.set_status(
        db,
        caller_id,
        body.status,
        reason=body.reason,
        by=body.by,
        now=datetime.now(UTC),
    )
    await db.commit()
    (view,) = await _callers(db, settings, caller_id)
    return view


@router.delete(
    "/{caller_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    summary="Forget a number",
)
async def delete_caller(caller_id: uuid.UUID, db: SessionDep) -> None:
    """Deletes the number and everything known about it, including its
    no-shows and status history. Its orders stay, linked to no one."""
    last_four = await callers.delete_caller(db, caller_id)
    await db.commit()
    # The number itself is gone; the log says only that one was deleted.
    logger.info("Deleted the caller ending %s", last_four)
