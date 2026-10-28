"""Session summaries: the note written about each session after it ends.

    GET  /summaries                     the latest, newest session first
    GET  /sessions/{session_id}/summary one, with the facts behind it
    POST /sessions/{session_id}/summary write it again now

Summaries are written by a background task a few seconds after a session
ends (see agent/summarizer.py). There is no sign-in: this is a local demo.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from drivetruagent.agent import summarizer
from drivetruagent.agent.factory import summary_model_from_settings
from drivetruagent.agent.model import ModelClient
from drivetruagent.agent.summarizer import SummaryDetail, SummaryView
from drivetruagent.api.deps import SessionDep, SettingsDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND
from drivetruagent.enums import SessionState

router = APIRouter(tags=["summaries"])


def get_summary_model(request: Request, settings: SettingsDep) -> ModelClient | None:
    """Made once and kept, like the background task's."""
    state = request.app.state
    if not hasattr(state, "summary_model"):
        state.summary_model = summary_model_from_settings(settings)
    return state.summary_model


SummaryModelDep = Annotated[ModelClient | None, Depends(get_summary_model)]


@router.get("/summaries", summary="The latest session summaries")
async def read_summaries(
    db: SessionDep,
    outcome: SessionState | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SummaryView]:
    """Newest session first; `outcome` keeps only sessions that ended that way."""
    return await summarizer.list_summaries(db, outcome=outcome, limit=limit)


@router.get(
    "/sessions/{session_id}/summary",
    responses=NOT_FOUND,
    summary="One session's summary",
)
async def read_summary(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> SummaryDetail:
    """404 until it has been written, a few seconds after the session ends."""
    return await summarizer.get_summary(db, session_id, prices=settings.model_prices)


@router.post(
    "/sessions/{session_id}/summary",
    responses=NOT_FOUND | CONFLICT,
    summary="Write a session's summary again",
)
async def rewrite_summary(
    session_id: uuid.UUID,
    db: SessionDep,
    settings: SettingsDep,
    model: SummaryModelDep,
) -> SummaryDetail:
    """Replaces the summary with a fresh one, or writes it if there was none.
    409 while the session is still going."""
    await summarizer.summarize(
        db, session_id, model, settings=settings.summary_settings, rewrite=True
    )
    return await summarizer.get_summary(db, session_id, prices=settings.model_prices)
