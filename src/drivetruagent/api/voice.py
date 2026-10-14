"""Voice calls: the browser sends its WebRTC offer here, not to OpenAI.

    POST   /sessions/{id}/voice    the browser's SDP offer in, OpenAI's answer out
    DELETE /sessions/{id}/voice    hang up

The backend forwards the offer to OpenAI with its own key and the call's
settings, hands the browser OpenAI's answer, and joins the call to run its
tools. The browser never holds a key. See agent/voice.py.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.factory import agent_config
from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.agent.prompt import build_instructions
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.agent.voice import (
    CallControl,
    LiveCalls,
    RealtimeClient,
    SessionScope,
    VoiceBridge,
    VoiceCallInProgress,
    create_call,
    realtime_client,
    run_call,
    session_config,
)
from drivetruagent.api.deps import SessionDep, SettingsDep, SpecialsCacheDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND, UNAVAILABLE
from drivetruagent.enums import FINAL_SESSION_STATES
from drivetruagent.ordering.errors import SessionEnded
from drivetruagent.ordering.sessions import get_session

router = APIRouter(tags=["voice"])

SDP = "application/sdp"


def get_realtime_client(settings: SettingsDep) -> RealtimeClient:
    """OpenAI, for voice. Tests replace this with a stand-in."""
    if settings.openai_api_key is None:
        raise ModelUnavailable(
            "Voice uses OpenAI Realtime and needs OPENAI_API_KEY, whichever "
            "model answers typed messages."
        )
    return realtime_client(settings.openai_api_key.get_secret_value())


def get_live_calls(request: Request) -> LiveCalls:
    """The app's calls in progress, created on first use."""
    calls: LiveCalls | None = getattr(request.app.state, "live_calls", None)
    if calls is None:
        calls = LiveCalls()
        request.app.state.live_calls = calls
    return calls


def get_session_scope(request: Request) -> SessionScope:
    """Where a call opens its own database sessions. A call outlives the
    request that started it, so it cannot use the request's session."""
    return request.app.state.session_factory


RealtimeDep = Annotated[RealtimeClient, Depends(get_realtime_client)]
LiveCallsDep = Annotated[LiveCalls, Depends(get_live_calls)]
ScopeDep = Annotated[SessionScope, Depends(get_session_scope)]
Offer = Annotated[
    str,
    Body(media_type=SDP, description="The browser's WebRTC offer, as SDP text."),
]


@router.post(
    "/sessions/{session_id}/voice",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | CONFLICT | UNAVAILABLE,
    response_class=Response,
    summary="Start a voice call",
)
async def start_call(
    session_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    client: RealtimeDep,
    calls: LiveCallsDep,
    scope: ScopeDep,
    specials_cache: SpecialsCacheDep,
) -> Response:
    """Send the browser's SDP offer; get OpenAI's SDP answer back.

    The call is set up with the same instructions and tools as text mode,
    and the backend joins it to run every tool call through the policy
    gateway.
    """
    offer = (await request.body()).decode("utf-8", errors="replace")
    if not offer.startswith("v="):
        return Response(
            "The body must be the browser's SDP offer.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            media_type="text/plain",
        )
    customer_session = await get_session(db, session_id)
    if customer_session.state in FINAL_SESSION_STATES:
        raise SessionEnded(customer_session.state)
    if calls.is_live(session_id):
        raise VoiceCallInProgress()

    instructions = await build_instructions(db, settings.restaurant_name)
    call_id, answer = await create_call(
        client,
        offer,
        session_config(instructions, TOOL_DEFINITIONS, settings.voice_settings),
    )
    config = agent_config(settings, specials_cache=specials_cache)

    def bridge_for(call: CallControl) -> VoiceBridge:
        return VoiceBridge(
            session_id=session_id,
            scope=scope,
            call=call,
            config=config,
            model_name=settings.voice_model,
        )

    calls.start(session_id, call_id, run_call(client, call_id, bridge_for))
    return Response(answer, status_code=status.HTTP_201_CREATED, media_type=SDP)


@router.delete(
    "/sessions/{session_id}/voice",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND | UNAVAILABLE,
    summary="Hang up",
)
async def hang_up(
    session_id: uuid.UUID, client: RealtimeDep, calls: LiveCallsDep
) -> Response:
    await calls.hang_up(session_id, client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@asynccontextmanager
async def borrowed(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A scope that hands out an existing session and leaves it open. For
    tests, where everything shares the test's own session."""
    yield db
