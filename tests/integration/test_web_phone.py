"""The web phone: a browser's offer in, answered as a phone call.

OpenAI is replaced by a stand-in that answers the offer, lets the backend
join the call, and records what it was asked to say.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import voice
from drivetruagent.agent.phone import greeting
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.agent.voice import LiveCalls
from drivetruagent.api.voice import (
    borrowed,
    get_live_calls,
    get_realtime_client,
    get_session_scope,
)
from drivetruagent.api.web_phone import WebPhoneLines, get_web_phone_lines
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import Caller, CustomerSession
from drivetruagent.enums import CallerStatus, Channel, SessionState
from drivetruagent.ordering.callers import set_status
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.caller_rules import TURN_AWAY_SENTENCES, TurnAway
from tests.integration.conftest import TEST_TAX_RATE

pytestmark = pytest.mark.usefixtures("menu")

KEY = "a-long-random-demo-key"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
ANSWER = "v=0\r\no=- 3 4 IN IP4 127.0.0.1\r\ns=answer\r\n"
CALL_ID = "rtc_web123"
CALLER = "+16475550123"
OTHER_CALLER = "+14165550188"


class StandInOpenAI:
    """Answers the offer and lets the backend join. Records what was said."""

    def __init__(self, *, call_stays_open: bool = False, refuse: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self.joined: list[str] = []
        self.said: list[str] = []
        self.hung_up: list[str] = []
        self.calls = LiveCalls()
        self._stays_open = call_stays_open
        self._refuse = refuse
        self.realtime = SimpleNamespace(
            calls=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=self._create),
                hangup=self._hangup,
            ),
            connect=self._connect,
        )

    async def _create(self, **request: Any) -> Any:
        if self._refuse:
            raise openai.OpenAIError("Connection refused.")
        self.created.append(request)
        return SimpleNamespace(
            headers={"location": f"/v1/realtime/calls/{CALL_ID}"}, text=ANSWER
        )

    async def _hangup(self, call_id: str) -> None:
        self.hung_up.append(call_id)

    def _connect(self, *, call_id: str) -> Any:
        self.joined.append(call_id)
        said = self.said
        stays_open = self._stays_open

        async def create(**request: Any) -> None:
            said.append(request.get("response", {}).get("instructions", ""))

        @asynccontextmanager
        async def joined() -> AsyncIterator[Any]:
            class Connection:
                response = SimpleNamespace(create=create)
                conversation = SimpleNamespace(item=SimpleNamespace(create=create))

                def __aiter__(self) -> "Connection":
                    return self

                async def __anext__(self) -> Any:
                    if stays_open:
                        await asyncio.Event().wait()
                    raise StopAsyncIteration

            yield Connection()

        return joined()


def use(
    app: FastAPI, db_session: AsyncSession, stand_in: StandInOpenAI, **settings: Any
) -> None:
    app.dependency_overrides[get_realtime_client] = lambda: stand_in
    app.dependency_overrides[get_live_calls] = lambda: stand_in.calls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)
    lines = WebPhoneLines()
    app.dependency_overrides[get_web_phone_lines] = lambda: lines
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        tax_rate=TEST_TAX_RATE,
        openai_api_key=None,
        **({"web_phone_key": KEY, "web_phone_caller": CALLER} | settings),
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )


@pytest.fixture
def stand_in(app: FastAPI, db_session: AsyncSession) -> StandInOpenAI:
    stand_in = StandInOpenAI()
    use(app, db_session, stand_in)
    return stand_in


@pytest.fixture
def quick_goodbyes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hang up straight after a sentence instead of waiting for it to be said."""
    monkeypatch.setattr(voice, "speaking_seconds", lambda _text: 0)


async def ring(
    client: httpx.AsyncClient,
    *,
    key: str | None = KEY,
    offer: str = OFFER,
    line: int | None = None,
) -> httpx.Response:
    headers = {"Content-Type": "application/sdp"}
    if key is not None:
        headers["X-Web-Phone-Key"] = key
    params = {} if line is None else {"line": line}
    return await client.post(
        "/web-phone/calls", content=offer, headers=headers, params=params
    )


async def connect(
    stand_in: StandInOpenAI, client: httpx.AsyncClient, call_key: str
) -> None:
    """Say the audio is up, and wait for the call to finish: it runs in the
    background on the test's one database session."""
    response = await client.post(
        f"/web-phone/calls/{call_key}/connected", headers={"X-Web-Phone-Key": KEY}
    )
    assert response.status_code == 204
    await stand_in.calls.wait()


async def sessions_in(db: AsyncSession) -> list[CustomerSession]:
    return list(
        (
            await db.execute(
                select(CustomerSession).execution_options(populate_existing=True)
            )
        ).scalars()
    )


# --- Only with the key ------------------------------------------------------


async def test_the_web_phone_is_off_without_a_key(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    use(app, db_session, StandInOpenAI(), web_phone_key=None)

    response = await ring(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "web_phone_off"


@pytest.mark.parametrize("key", [None, "", "a-long-random-demo-kez"])
async def test_a_call_without_the_right_key_is_refused(
    client: httpx.AsyncClient,
    stand_in: StandInOpenAI,
    db_session: AsyncSession,
    key: str | None,
) -> None:
    response = await ring(client, key=key)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "wrong_web_phone_key"
    assert stand_in.created == []
    assert await sessions_in(db_session) == []


async def test_the_page_can_check_its_key_and_learn_who_it_calls(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    right = await client.get("/web-phone/line", headers={"X-Web-Phone-Key": KEY})
    wrong = await client.get("/web-phone/line", headers={"X-Web-Phone-Key": "nope"})

    assert (right.status_code, right.json()) == (
        200,
        {
            "restaurant_name": "Maple Lane Burgers",
            "lines": [{"line": 1, "caller_last_four": "0123", "busy": False}],
        },
    )
    assert wrong.status_code == 403


async def test_hanging_up_and_connecting_need_the_key_too(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    call_key = uuid.uuid4()

    connected = await client.post(f"/web-phone/calls/{call_key}/connected")
    hung_up = await client.delete(f"/web-phone/calls/{call_key}")

    assert (connected.status_code, hung_up.status_code) == (403, 403)


async def test_the_body_must_be_an_offer(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await ring(client, offer="hello")

    assert response.status_code == 422
    assert stand_in.created == []


# --- Answered as a phone call -----------------------------------------------


async def test_a_call_is_answered_as_a_phone_order_from_the_set_number(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    response = await ring(client)

    assert response.status_code == 201
    body = response.json()
    (session,) = await sessions_in(db_session)
    assert body == {
        "call_key": str(session.id),
        "answer_sdp": ANSWER,
        "session_id": str(session.id),
        "turned_away": None,
    }
    assert session.channel is Channel.PHONE
    caller = await db_session.get(Caller, session.caller_id)
    assert caller is not None and caller.phone_e164 == CALLER
    await connect(stand_in, client, body["call_key"])


async def test_the_call_is_set_up_for_pickup_with_every_tool(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await ring(client)

    (request,) = stand_in.created
    assert request["sdp"] == OFFER
    session = request["session"]
    assert session["instructions"].startswith("You take phone orders for pickup")
    assert len(session["tools"]) == len(TOOL_DEFINITIONS)
    await connect(stand_in, client, response.json()["call_key"])


async def test_nothing_is_said_until_the_browsers_audio_is_up(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await ring(client)
    await asyncio.sleep(0.05)

    assert (stand_in.joined, stand_in.said) == ([], [])

    await connect(stand_in, client, response.json()["call_key"])

    notice = greeting("Maple Lane Burgers", number_known=True)
    assert stand_in.joined == [CALL_ID]
    assert len(stand_in.said) == 1 and stand_in.said[0].endswith(notice)


async def test_the_call_is_on_the_live_screens_list(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await ring(client)

    (listed,) = (await client.get("/phone/calls")).json()
    assert listed["session_id"] == response.json()["session_id"]
    assert listed["caller_last_four"] == "0123"
    await connect(stand_in, client, response.json()["call_key"])


async def test_without_a_set_number_the_call_is_from_a_withheld_number(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    stand_in = StandInOpenAI()
    use(app, db_session, stand_in, web_phone_caller=None)

    response = await ring(client)
    await connect(stand_in, client, response.json()["call_key"])

    (session,) = await sessions_in(db_session)
    assert session.caller_id is None
    assert "keep your number" not in stand_in.said[0]


@pytest.mark.parametrize("setting", ["my cell", f"{CALLER},my cell"])
async def test_a_set_number_that_is_not_a_number_turns_the_web_phone_off(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession, setting: str
) -> None:
    use(app, db_session, StandInOpenAI(), web_phone_caller=setting)

    response = await ring(client)
    info = await client.get("/web-phone/line", headers={"X-Web-Phone-Key": KEY})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "web_phone_off"
    assert info.status_code == 503


async def test_a_call_openai_will_not_take_closes_its_session(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    use(app, db_session, StandInOpenAI(refuse=True))

    response = await ring(client)

    assert response.status_code == 503
    (session,) = await sessions_in(db_session)
    assert (session.state, session.end_reason) == (
        SessionState.ABANDONED,
        "call_not_answered",
    )


# --- A call at a time on each line, and hanging up --------------------------


async def test_a_second_call_on_a_line_in_use_is_refused(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    use(app, db_session, stand_in)

    first = await ring(client)
    second = await ring(client)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "web_phone_line_busy"
    assert len(stand_in.created) == 1
    await stand_in.calls.close_all()


async def test_each_number_is_a_line_and_the_page_sees_which_are_busy(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    use(app, db_session, stand_in, web_phone_caller=f"{CALLER}, 416 555 0188")

    await ring(client, line=2)
    info = await client.get("/web-phone/line", headers={"X-Web-Phone-Key": KEY})

    assert info.json()["lines"] == [
        {"line": 1, "caller_last_four": "0123", "busy": False},
        {"line": 2, "caller_last_four": "0188", "busy": True},
    ]
    await stand_in.calls.close_all()


async def test_calls_on_different_lines_go_on_at_once_from_their_own_numbers(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    use(app, db_session, stand_in, web_phone_caller=f"{CALLER},{OTHER_CALLER}")

    first = await ring(client, line=1)
    second = await ring(client, line=2)

    assert (first.status_code, second.status_code) == (201, 201)
    sessions = {session.id: session for session in await sessions_in(db_session)}
    numbers = []
    for response in (first, second):
        session = sessions[uuid.UUID(response.json()["session_id"])]
        caller = await db_session.get(Caller, session.caller_id)
        assert caller is not None
        numbers.append(caller.phone_e164)
    assert numbers == [CALLER, OTHER_CALLER]
    listed = (await client.get("/phone/calls")).json()
    assert {call["caller_last_four"] for call in listed} == {"0123", "0188"}
    await stand_in.calls.close_all()


async def test_a_line_that_is_not_set_up_is_not_found(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await ring(client, line=2)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_such_web_phone_line"
    assert stand_in.created == []


async def test_once_the_first_call_is_over_the_line_is_free(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    first = await ring(client)
    await connect(stand_in, client, first.json()["call_key"])

    second = await ring(client)

    assert second.status_code == 201
    await connect(stand_in, client, second.json()["call_key"])


async def test_hanging_up_ends_the_call_at_openai(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    stand_in = StandInOpenAI(call_stays_open=True)
    use(app, db_session, stand_in)
    call_key = (await ring(client)).json()["call_key"]

    response = await client.delete(
        f"/web-phone/calls/{call_key}", headers={"X-Web-Phone-Key": KEY}
    )

    assert response.status_code == 204
    assert stand_in.hung_up == [CALL_ID]
    await stand_in.calls.close_all()


async def test_hanging_up_a_call_that_is_not_going_is_not_found(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    response = await client.delete(
        f"/web-phone/calls/{uuid.uuid4()}", headers={"X-Web-Phone-Key": KEY}
    )

    assert response.status_code == 404


# --- The same caller rules as the phone line --------------------------------


async def block(db: AsyncSession, phone: str) -> None:
    await open_session(db, channel=Channel.PHONE, caller_phone=phone)
    caller = (
        await db.execute(select(Caller).where(Caller.phone_e164 == phone))
    ).scalar_one()
    await set_status(
        db,
        caller.id,
        CallerStatus.BLOCKED,
        reason="Abusive calls",
        by="Manager",
        now=datetime.now(UTC),
    )
    await db.commit()


@pytest.mark.usefixtures("quick_goodbyes")
async def test_a_blocked_number_hears_why_and_never_reaches_the_agent(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    await block(db_session, CALLER)

    response = await ring(client)
    body = response.json()
    await connect(stand_in, client, body["call_key"])

    assert response.status_code == 201
    assert (body["session_id"], body["turned_away"]) == (None, "blocked")
    assert body["answer_sdp"] == ANSWER
    # No tools: nothing the caller says can reach the menu or an order.
    assert "tools" not in stand_in.created[0]["session"]
    assert stand_in.said == [
        "Say exactly this, in the same language, and nothing else: "
        + TURN_AWAY_SENTENCES[TurnAway.BLOCKED]
    ]
    assert stand_in.hung_up == [CALL_ID]
    # Only the session opened by block(); the refused call opened none.
    assert len(await sessions_in(db_session)) == 1


@pytest.mark.usefixtures("quick_goodbyes")
async def test_a_number_calling_again_and_again_without_ordering_is_turned_away(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    for _ in range(5):
        await open_session(db_session, channel=Channel.PHONE, caller_phone=CALLER)
    await db_session.commit()

    response = await ring(client)
    await connect(stand_in, client, response.json()["call_key"])

    assert response.json()["turned_away"] == "too_many_calls"
    assert stand_in.said[0].endswith(TURN_AWAY_SENTENCES[TurnAway.TOO_MANY_CALLS])
