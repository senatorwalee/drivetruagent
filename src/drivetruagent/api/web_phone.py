"""The web phone: ring the agent from a phone's browser, without a number.

    GET    /web-phone/line                   who answers; also checks the key
    POST   /web-phone/calls                  the browser's SDP offer in, the call out
    POST   /web-phone/calls/{key}/connected  the browser's audio is up
    DELETE /web-phone/calls/{key}            hang up

For demos where there is no phone line. The call is a phone call in every
way but how it arrives: the same notice, session, rules and log. Every
request carries WEB_PHONE_KEY, since these endpoints are reachable through
the tunnel and each call spends the OpenAI key. The caller's number comes
from WEB_PHONE_CALLER, never from the browser. See agent/phone.py.
"""

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, Request, Response, status
from fastapi.routing import APIRouter
from pydantic import BaseModel

from drivetruagent.agent.factory import agent_config
from drivetruagent.agent.phone import answer_web_call
from drivetruagent.agent.voice import VoiceCallInProgress
from drivetruagent.api.deps import (
    BusinessDateDep,
    SessionDep,
    SettingsDep,
    SpecialsCacheDep,
)
from drivetruagent.api.errors import (
    CONFLICT,
    FORBIDDEN,
    NOT_FOUND,
    REFUSED,
    UNAVAILABLE,
)
from drivetruagent.api.voice import LiveCallsDep, RealtimeDep, ScopeDep
from drivetruagent.ordering.errors import OrderingError
from drivetruagent.ordering.phone_numbers import normalize_phone
from drivetruagent.policy.caller_rules import TurnAway

router = APIRouter(tags=["web phone"])


class WebPhoneOff(OrderingError):
    code = "web_phone_off"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class WrongWebPhoneKey(OrderingError):
    code = "wrong_web_phone_key"

    def __init__(self) -> None:
        super().__init__("This link's key is missing or out of date.")


class NotAnOffer(OrderingError):
    code = "not_an_offer"

    def __init__(self) -> None:
        super().__init__("The body must be the browser's SDP offer.")


@dataclass
class WebPhoneLine:
    """The web phone's one line: a call at a time, which also caps what a
    leaked link can spend."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current: uuid.UUID | None = None
    connected: dict[uuid.UUID, asyncio.Event] = field(default_factory=dict)


def get_web_phone_line(request: Request) -> WebPhoneLine:
    line: WebPhoneLine | None = getattr(request.app.state, "web_phone_line", None)
    if line is None:
        line = WebPhoneLine()
        request.app.state.web_phone_line = line
    return line


def require_key(
    settings: SettingsDep,
    key: Annotated[str | None, Header(alias="X-Web-Phone-Key")] = None,
) -> None:
    if settings.web_phone_key is None:
        raise WebPhoneOff("The web phone is off: set WEB_PHONE_KEY.")
    expected = settings.web_phone_key.get_secret_value().encode()
    if key is None or not secrets.compare_digest(key.encode(), expected):
        raise WrongWebPhoneKey()


LineDep = Annotated[WebPhoneLine, Depends(get_web_phone_line)]
Keyed = [Depends(require_key)]


class WebPhoneInfo(BaseModel):
    # Who the call goes to, for the page to show before it rings.
    restaurant_name: str


@router.get(
    "/web-phone/line",
    dependencies=Keyed,
    responses=FORBIDDEN | UNAVAILABLE,
    summary="Who the web phone calls",
)
async def web_phone_line(settings: SettingsDep) -> WebPhoneInfo:
    """Lets the page show who it calls, and find out a wrong key before
    anyone taps Call."""
    return WebPhoneInfo(restaurant_name=settings.restaurant_name)


class WebCall(BaseModel):
    # What the call is called in the other two endpoints.
    call_key: uuid.UUID
    # OpenAI's answer, for the browser's WebRTC connection.
    answer_sdp: str
    # The session the call is handled in; None if it was turned away.
    session_id: uuid.UUID | None
    turned_away: TurnAway | None = None


@router.post(
    "/web-phone/calls",
    status_code=status.HTTP_201_CREATED,
    dependencies=Keyed,
    responses=FORBIDDEN | CONFLICT | REFUSED | UNAVAILABLE,
    summary="Ring the agent from the web phone",
)
async def start_web_call(
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    today: BusinessDateDep,
    client: RealtimeDep,
    calls: LiveCallsDep,
    scope: ScopeDep,
    specials_cache: SpecialsCacheDep,
    line: LineDep,
) -> WebCall:
    """Answered like a phone call from WEB_PHONE_CALLER. Refused while the
    last web call is still going."""
    offer = (await request.body()).decode("utf-8", errors="replace")
    if not offer.startswith("v="):
        raise NotAnOffer()
    caller_phone = None
    if settings.web_phone_caller is not None:
        caller_phone = normalize_phone(settings.web_phone_caller)
        if caller_phone is None:
            raise WebPhoneOff("WEB_PHONE_CALLER is not a phone number.")
    async with line.lock:
        if line.current is not None and calls.is_live(line.current):
            raise VoiceCallInProgress()
        connected = asyncio.Event()
        outcome = await answer_web_call(
            db,
            offer,
            caller_phone,
            connected=connected,
            client=client,
            calls=calls,
            scope=scope,
            config=agent_config(settings, specials_cache=specials_cache),
            voice=settings.voice_settings,
            business_date=today,
            caller_limits=settings.caller_limits,
        )
        line.current = outcome.call_key
        line.connected = {outcome.call_key: connected}
    return WebCall(
        call_key=outcome.call_key,
        answer_sdp=outcome.answer_sdp,
        session_id=outcome.session_id,
        turned_away=outcome.turned_away,
    )


@router.post(
    "/web-phone/calls/{call_key}/connected",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=Keyed,
    responses=FORBIDDEN | UNAVAILABLE,
    summary="The web phone's audio is connected",
)
async def web_call_connected(call_key: uuid.UUID, line: LineDep) -> Response:
    """The agent starts talking now. A call that is over, or unknown, is
    already done with: nothing to do."""
    event = line.connected.get(call_key)
    if event is not None:
        event.set()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/web-phone/calls/{call_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=Keyed,
    responses=FORBIDDEN | NOT_FOUND | UNAVAILABLE,
    summary="Hang up the web phone",
)
async def hang_up_web_call(
    call_key: uuid.UUID, client: RealtimeDep, calls: LiveCallsDep
) -> Response:
    await calls.hang_up(call_key, client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
