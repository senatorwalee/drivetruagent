"""Reading a phone number's standing from the database.

The rules are in caller_rules.py and take plain counts; this is where the
counts come from, for the gateway (big orders) and the phone line (calls
turned away).
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import CustomerSession
from drivetruagent.enums import Channel
from drivetruagent.ordering.callers import CallerSummary, caller_summaries
from drivetruagent.policy.caller_rules import (
    CallerCounts,
    CallerLimits,
    Standing,
    standing,
)


def counts_of(summary: CallerSummary) -> CallerCounts:
    return CallerCounts(
        status=summary.status,
        recent_no_shows=summary.recent_no_shows,
        recent_calls_without_order=summary.recent_calls_without_order,
    )


async def counts_for(
    db: AsyncSession,
    limits: CallerLimits,
    *,
    now: datetime,
    phone_e164: str | None = None,
    caller_id: uuid.UUID | None = None,
) -> CallerCounts | None:
    """The counts for one caller, by number or id. None if there is no such
    caller yet: a first call has nothing against it."""
    if phone_e164 is None and caller_id is None:
        raise ValueError("Give a number or a caller id.")
    found = await caller_summaries(
        db,
        now=now,
        no_show_window=limits.no_show_window,
        repeat_call_window=limits.repeat_call_window,
        phone_e164=phone_e164,
        caller_id=caller_id,
        limit=1,
    )
    return counts_of(found[0]) if found else None


async def session_standing(
    db: AsyncSession,
    customer_session: CustomerSession,
    limits: CallerLimits,
    *,
    now: datetime,
) -> Standing | None:
    """The standing of the session's caller; None for a drive-through car."""
    if customer_session.channel is not Channel.PHONE:
        return None
    if customer_session.caller_id is None:
        return Standing.UNKNOWN
    counts = await counts_for(db, limits, now=now, caller_id=customer_session.caller_id)
    # The caller may have been deleted mid-call, which leaves nothing to go on.
    return standing(counts, limits)
