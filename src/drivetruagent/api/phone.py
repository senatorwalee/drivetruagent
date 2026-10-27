"""The phone line.

    POST /phone/incoming   OpenAI's webhook: a call is ringing; answer it
    GET  /phone/calls      calls in progress, for the live screen

A phone number's SIP trunk sends calls to OpenAI, and OpenAI tells this
endpoint about each one with a signed webhook. Only a correctly signed,
recent request answers a call; the caller's number is read from it, never
from anything a client can type. See agent/phone.py.
"""

import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from drivetruagent.agent.factory import agent_config
from drivetruagent.agent.phone import answer_call
from drivetruagent.agent.voice import InvalidWebhook, read_incoming_call
from drivetruagent.api.deps import (
    BusinessDateDep,
    SessionDep,
    SettingsDep,
    SpecialsCacheDep,
)
from drivetruagent.api.errors import UNAVAILABLE, ErrorBody, ErrorResponse
from drivetruagent.api.voice import LiveCallsDep, RealtimeDep, ScopeDep
from drivetruagent.ordering.callers import ActiveCall, active_calls
from drivetruagent.policy.caller_rules import TurnAway

router = APIRouter(tags=["phone"])


class CallAnswered(BaseModel):
    # The session the call is being handled in. None: nothing to answer (not
    # a call, or a call already being answered), or the call was turned away.
    session_id: uuid.UUID | None
    # Why the call was told it can't be taken, if it was.
    turned_away: TurnAway | None = None


def _refusal(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details={}))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


@router.post(
    "/phone/incoming",
    # Declared, because a refusal is returned as a plain JSON response.
    response_model=CallAnswered,
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}} | UNAVAILABLE,
    summary="Answer a phone call (OpenAI's webhook)",
)
async def incoming_call(
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    today: BusinessDateDep,
    client: RealtimeDep,
    calls: LiveCallsDep,
    scope: ScopeDep,
    specials_cache: SpecialsCacheDep,
) -> CallAnswered | JSONResponse:
    """OpenAI calls this when someone rings the restaurant's number. The call
    is answered with the agent's instructions and tools, the caller hears
    the opening notice, and the backend handles the call from then on."""
    if settings.openai_webhook_secret is None:
        return _refusal(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "phone_line_off",
            "The phone line is off: set OPENAI_WEBHOOK_SECRET.",
        )
    try:
        incoming = read_incoming_call(
            client,
            await request.body(),
            request.headers,
            settings.openai_webhook_secret.get_secret_value(),
        )
    except InvalidWebhook as error:
        return _refusal(status.HTTP_400_BAD_REQUEST, "invalid_webhook", str(error))
    if incoming is None:
        return CallAnswered(session_id=None)

    outcome = await answer_call(
        db,
        incoming,
        client=client,
        calls=calls,
        scope=scope,
        config=agent_config(settings, specials_cache=specials_cache),
        voice=settings.voice_settings,
        business_date=today,
        staff_phone=settings.staff_phone_number,
        caller_limits=settings.caller_limits,
    )
    return CallAnswered(session_id=outcome.session_id, turned_away=outcome.turned_away)


@router.get("/phone/calls", summary="Phone calls in progress")
async def phone_calls(db: SessionDep) -> list[ActiveCall]:
    """Newest first. The live screen follows the newest one."""
    return await active_calls(db)
