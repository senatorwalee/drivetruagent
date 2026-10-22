"""Insights: what guests are telling the restaurant. Read only."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from drivetruagent.agent.insights import UnmetRequest, unmet_requests
from drivetruagent.api.deps import SessionDep

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get(
    "/unmet-requests", summary="What guests asked for that the menu couldn't fill"
)
async def read_unmet_requests(
    db: SessionDep,
    days: Annotated[int, Query(ge=1, le=90, description="How far back to look.")] = 7,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[UnmetRequest]:
    """Requests whose best menu match was only close ("oat milk shake") or
    nothing at all ("gluten-free bun"), most asked first."""
    since = datetime.now(UTC) - timedelta(days=days)
    return await unmet_requests(db, since=since, limit=limit)
