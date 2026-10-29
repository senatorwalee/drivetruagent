"""The web phone: ring the agent from a phone's browser, without a number.

    GET    /web-phone/line                   who answers, and the lines; checks the key
    POST   /web-phone/calls?line=N           the browser's SDP offer in, the call out
    POST   /web-phone/calls/{key}/connected  the browser's audio is up
    DELETE /web-phone/calls/{key}            hang up

For demos where there is no phone line. The call is a phone call in every
way but how it arrives: the same notice, session, rules and log. Every
request carries WEB_PHONE_KEY, since these endpoints are reachable through
the tunnel and each call spends the OpenAI key. The caller's number comes
from WEB_PHONE_CALLER, never from the browser: the browser only picks a line,
and each number set there is a line of its own, taking one call at a time.
See agent/phone.py.
"""

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, Query, Request, Response, status
from fastapi.routing import APIRouter
from pydantic import BaseModel

from drivetruagent.agent.factory import agent_config
from drivetruagent.agent.phone import answer_web_call
from drivetruagent.agent.voice import LiveCalls
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


class NoSuchLine(OrderingError):
    code = "no_such_web_phone_line"

    def __init__(self, line: int) -> None:
        super().__init__(f"The web phone has no line {line}.", line=line)


class LineBusy(OrderingError):
    code = "web_phone_line_busy"

    def __init__(self, line: int) -> None:
        super().__init__(
            f"Line {line} is already on a call. Pick another, or wait for it to end.",
            line=line,
        )


class NotAnOffer(OrderingError):
    code = "not_an_offer"

    def __init__(self) -> None:
        super().__init__("The body must be the browser's SDP offer.")


@dataclass
class WebPhoneLines:
    """The web phone's lines, one per number: a call at a time on each, which
    also caps what a leaked link can spend."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The call on each line, by line number (from 1).
    current: dict[int, uuid.UUID] = field(default_factory=dict)
    connected: dict[uuid.UUID, asyncio.Event] = field(default_factory=dict)

    def busy(self, line: int, calls: LiveCalls) -> bool:
        call_key = self.current.get(line)
        return call_key is not None and calls.is_live(call_key)


def get_web_phone_lines(request: Request) -> WebPhoneLines:
    lines: WebPhoneLines | None = getattr(request.app.state, "web_phone_lines", None)
    if lines is None:
        lines = WebPhoneLines()
        request.app.state.web_phone_lines = lines
    return lines


def caller_numbers(setting: str | None) -> list[str | None]:
    """The number each line calls from, from WEB_PHONE_CALLER: one number, or
    several separated by commas. Unset: one line, from a withheld number."""
    if setting is None:
        return [None]
    numbers: list[str | None] = []
    for written in filter(None, (part.strip() for part in setting.split(","))):
        number = normalize_phone(written)
        if number is None:
            raise WebPhoneOff("WEB_PHONE_CALLER holds something that is not a number.")
        if number not in numbers:
            numbers.append(number)
    if not numbers:
        return [None]
    return numbers


def require_key(
    settings: SettingsDep,
    key: Annotated[str | None, Header(alias="X-Web-Phone-Key")] = None,
) -> None:
    if settings.web_phone_key is None:
        raise WebPhoneOff("The web phone is off: set WEB_PHONE_KEY.")
    expected = settings.web_phone_key.get_secret_value().encode()
    if key is None or not secrets.compare_digest(key.encode(), expected):
        raise WrongWebPhoneKey()


LinesDep = Annotated[WebPhoneLines, Depends(get_web_phone_lines)]
Keyed = [Depends(require_key)]


class WebPhoneLine(BaseModel):
    line: int
    # The last four digits of the number it calls from, to tell the lines
    # apart; None for a withheld number. The page never gets the whole number.
    caller_last_four: str | None
    busy: bool


class WebPhoneInfo(BaseModel):
    # Who the call goes to, for the page to show before it rings.
    restaurant_name: str
    lines: list[WebPhoneLine]


@router.get(
    "/web-phone/line",
    dependencies=Keyed,
    responses=FORBIDDEN | UNAVAILABLE,
    summary="Who the web phone calls",
)
async def web_phone_line(
    settings: SettingsDep, calls: LiveCallsDep, lines: LinesDep
) -> WebPhoneInfo:
    """Lets the page show who it calls and which lines are free, and find out
    a wrong key before anyone taps Call."""
    numbers = caller_numbers(settings.web_phone_caller)
    return WebPhoneInfo(
        restaurant_name=settings.restaurant_name,
        lines=[
            WebPhoneLine(
                line=line,
                caller_last_four=None if number is None else number[-4:],
                busy=lines.busy(line, calls),
            )
            for line, number in enumerate(numbers, start=1)
        ],
    )


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
    responses=FORBIDDEN | NOT_FOUND | CONFLICT | REFUSED | UNAVAILABLE,
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
    lines: LinesDep,
    line: Annotated[int, Query(ge=1)] = 1,
) -> WebCall:
    """Answered like a phone call from the line's number in WEB_PHONE_CALLER.
    Refused while the line's last call is still going."""
    offer = (await request.body()).decode("utf-8", errors="replace")
    if not offer.startswith("v="):
        raise NotAnOffer()
    numbers = caller_numbers(settings.web_phone_caller)
    if line > len(numbers):
        raise NoSuchLine(line)
    caller_phone = numbers[line - 1]
    async with lines.lock:
        if lines.busy(line, calls):
            raise LineBusy(line)
        # Forget the calls that are over.
        lines.connected = {
            key: event for key, event in lines.connected.items() if calls.is_live(key)
        }
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
        lines.current[line] = outcome.call_key
        lines.connected[outcome.call_key] = connected
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
async def web_call_connected(call_key: uuid.UUID, lines: LinesDep) -> Response:
    """The agent starts talking now. A call that is over, or unknown, is
    already done with: nothing to do."""
    event = lines.connected.get(call_key)
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
