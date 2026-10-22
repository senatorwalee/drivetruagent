"""What guests asked for that the menu couldn't fill, read from the event log.

Every menu search the agent makes is already in the log with its result. A
search whose best match was "close" (an oat milk shake, spicy nuggets) or
"none" (a gluten-free bun) is a request the restaurant couldn't fill. Counted
across sessions, it says what guests want that isn't on the menu.
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import AgentEvent
from drivetruagent.enums import AgentEventKind

UNMET = ("close", "none")


class UnmetRequest(BaseModel):
    # The guest's words, lowercased and trimmed, so repeats count together.
    request: str
    times: int
    # Whether the closest item was close to it, or nothing was.
    match: str
    last_asked: datetime


async def unmet_requests(
    db: AsyncSession, *, since: datetime, limit: int = 20
) -> list[UnmetRequest]:
    """The most asked-for things the menu couldn't fill since `since`."""
    result = AgentEvent.payload["result"]
    request = func.lower(func.btrim(result["query"].astext))
    match = result["match"].astext
    rows = (
        await db.execute(
            select(
                request.label("request"),
                func.count().label("times"),
                func.max(match).label("match"),
                func.max(AgentEvent.created_at).label("last_asked"),
            )
            .where(
                AgentEvent.kind == AgentEventKind.TOOL_RESULT,
                AgentEvent.payload["name"].astext == "search_menu",
                match.in_(UNMET),
                AgentEvent.created_at >= since,
            )
            .group_by(request)
            .order_by(func.count().desc(), func.max(AgentEvent.created_at).desc())
            .limit(limit)
        )
    ).all()
    return [
        UnmetRequest(
            request=r.request, times=r.times, match=r.match, last_asked=r.last_asked
        )
        for r in rows
    ]
