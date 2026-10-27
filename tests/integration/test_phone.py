"""The phone line: OpenAI's signed webhook in, a call answered, the notice said.

OpenAI itself is replaced by a stand-in, except for checking signatures,
which uses OpenAI's own library on requests signed here the way OpenAI signs
them.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.phone import greeting
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.agent.voice import LiveCalls
from drivetruagent.api.voice import (
    borrowed,
    get_live_calls,
    get_realtime_client,
    get_session_scope,
)
from drivetruagent.config import Settings, get_settings
from drivetruagent.db.models import AgentEvent, Caller, CustomerSession
from drivetruagent.enums import AgentEventKind, Channel, SessionState
from tests.integration.conftest import TEST_TAX_RATE

pytestmark = pytest.mark.usefixtures("menu")

SECRET = "whsec_" + base64.b64encode(b"a test signing key, 32 bytes long").decode()
CALL_ID = "rtc_phone123"


def signed(
    payload: dict[str, Any],
    *,
    secret: str = SECRET,
    webhook_id: str = "wh_1",
    sent_at: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    """A webhook body and headers, signed as OpenAI signs them."""
    body = json.dumps(payload).encode()
    timestamp = str(sent_at if sent_at is not None else int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(
        key, f"{webhook_id}.{timestamp}.{body.decode()}".encode(), hashlib.sha256
    ).digest()
    return body, {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
        "content-type": "application/json",
    }


def ringing(caller: str = "<sip:+14165550123@pstn.twilio.com>;tag=1") -> dict[str, Any]:
    return {
        "object": "event",
        "id": "evt_1",
        "type": "realtime.call.incoming",
        "created_at": int(time.time()),
        "data": {
            "call_id": CALL_ID,
            "sip_headers": [
                {"name": "From", "value": caller},
                {"name": "To", "value": "<sip:+16475550100@example.com>"},
            ],
        },
    }


class StandInOpenAI:
    """Takes the call, and lets the backend join it. Records what was said."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.accepted: list[tuple[str, dict[str, Any]]] = []
        self.said: list[str] = []
        self.joined: list[str] = []
        self.calls = LiveCalls()
        self._refuse = refuse
        # Signatures are checked by OpenAI's own code.
        self.webhooks = openai.AsyncOpenAI(api_key="unused").webhooks
        self.realtime = SimpleNamespace(
            calls=SimpleNamespace(
                accept=self._accept, hangup=self._noop, refer=self._noop
            ),
            connect=self._connect,
        )

    async def _accept(self, call_id: str, **session: Any) -> None:
        if self._refuse:
            raise openai.OpenAIError("Call not found.")
        self.accepted.append((call_id, session))

    async def _noop(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _connect(self, *, call_id: str) -> Any:
        self.joined.append(call_id)
        said = self.said

        async def create(**request: Any) -> None:
            instructions = request.get("response", {}).get("instructions", "")
            said.append(instructions)

        @asynccontextmanager
        async def joined() -> AsyncIterator[Any]:
            class Connection:
                response = SimpleNamespace(create=create)
                conversation = SimpleNamespace(item=SimpleNamespace(create=create))

                def __aiter__(self) -> "Connection":
                    return self

                async def __anext__(self) -> Any:
                    raise StopAsyncIteration

            yield Connection()

        return joined()


def use(
    app: FastAPI,
    db_session: AsyncSession,
    stand_in: StandInOpenAI,
    **settings: Any,
) -> LiveCalls:
    calls = stand_in.calls
    app.dependency_overrides[get_realtime_client] = lambda: stand_in
    app.dependency_overrides[get_live_calls] = lambda: calls
    app.dependency_overrides[get_session_scope] = lambda: lambda: borrowed(db_session)
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        tax_rate=TEST_TAX_RATE,
        openai_api_key=None,
        openai_webhook_secret=SECRET,
        **settings,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    return calls


@pytest.fixture
def stand_in(app: FastAPI, db_session: AsyncSession) -> StandInOpenAI:
    stand_in = StandInOpenAI()
    use(app, db_session, stand_in)
    return stand_in


async def deliver(
    stand_in: StandInOpenAI,
    client: httpx.AsyncClient,
    body: bytes,
    headers: dict[str, str],
) -> httpx.Response:
    """Send the webhook, and wait for the call it started to finish: it runs
    in the background on the test's one database session."""
    response = await client.post("/phone/incoming", content=body, headers=headers)
    await stand_in.calls.wait()
    return response


async def sessions_in(db: AsyncSession) -> list[CustomerSession]:
    return list(
        (
            await db.execute(
                select(CustomerSession).execution_options(populate_existing=True)
            )
        ).scalars()
    )


# --- Answering --------------------------------------------------------------


async def test_a_signed_call_is_answered_as_a_phone_order(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    response = await deliver(stand_in, client, *signed(ringing()))

    assert response.status_code == 200
    (session,) = await sessions_in(db_session)
    assert response.json()["session_id"] == str(session.id)
    assert session.channel is Channel.PHONE
    caller = await db_session.get(Caller, session.caller_id)
    assert caller is not None and caller.phone_e164 == "+14165550123"


async def test_the_call_is_set_up_for_pickup_with_every_tool(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    await deliver(stand_in, client, *signed(ringing()))

    ((call_id, session),) = stand_in.accepted
    assert call_id == CALL_ID
    assert session["type"] == "realtime"
    assert session["instructions"].startswith("You take phone orders for pickup")
    assert "pull forward" not in session["instructions"]
    assert "ready in about 15 minutes" in session["instructions"]
    assert len(session["tools"]) == len(TOOL_DEFINITIONS)


async def test_the_caller_hears_the_notice_first_and_it_is_logged(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    await deliver(stand_in, client, *signed(ringing()))

    notice = greeting("Maple Lane Burgers", number_known=True)
    assert stand_in.joined == [CALL_ID]
    assert len(stand_in.said) == 1 and stand_in.said[0].endswith(notice)
    logged = (
        await db_session.execute(
            select(AgentEvent).where(
                AgentEvent.kind == AgentEventKind.ASSISTANT_MESSAGE
            )
        )
    ).scalars()
    assert [event.payload["text"] for event in logged] == [notice]


@pytest.mark.parametrize(
    "caller", ["sip:anonymous@anonymous.invalid", "<sip:Restricted@x>"]
)
async def test_a_withheld_number_is_answered_without_a_caller(
    client: httpx.AsyncClient,
    stand_in: StandInOpenAI,
    db_session: AsyncSession,
    caller: str,
) -> None:
    await deliver(stand_in, client, *signed(ringing(caller)))

    (session,) = await sessions_in(db_session)
    assert (session.channel, session.caller_id) == (Channel.PHONE, None)
    assert "keep your number" not in stand_in.said[0]


async def test_a_webhook_delivered_twice_answers_the_call_once(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    first = await deliver(stand_in, client, *signed(ringing(), webhook_id="wh_1"))
    again = await deliver(stand_in, client, *signed(ringing(), webhook_id="wh_2"))

    assert first.json()["session_id"] is not None
    assert again.json() == {"session_id": None}
    assert len(stand_in.accepted) == 1
    assert len(await sessions_in(db_session)) == 1


async def test_other_events_are_acknowledged_and_ignored(
    client: httpx.AsyncClient, stand_in: StandInOpenAI, db_session: AsyncSession
) -> None:
    event = ringing() | {"type": "batch.completed", "data": {"id": "batch_1"}}

    response = await deliver(stand_in, client, *signed(event))

    assert (response.status_code, response.json()) == (200, {"session_id": None})
    assert await sessions_in(db_session) == []


async def test_a_call_openai_will_not_hand_over_closes_its_session(
    app: FastAPI, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    refusing = StandInOpenAI(refuse=True)
    use(app, db_session, refusing)

    response = await deliver(refusing, client, *signed(ringing()))

    assert response.status_code == 503
    (session,) = await sessions_in(db_session)
    assert (session.state, session.end_reason) == (
        SessionState.ABANDONED,
        "call_not_answered",
    )


# --- Only OpenAI can ring the line ------------------------------------------


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(
            lambda b, h: (b, h | {"webhook-signature": "v1,AAAA"}), id="forged"
        ),
        pytest.param(
            lambda b, h: (b.replace(b"+14165550123", b"+16475550199"), h),
            id="number_changed",
        ),
        pytest.param(
            lambda b, h: (b, {k: v for k, v in h.items() if k != "webhook-signature"}),
            id="unsigned",
        ),
    ],
)
async def test_a_request_not_signed_by_openai_is_refused(
    client: httpx.AsyncClient,
    stand_in: StandInOpenAI,
    db_session: AsyncSession,
    tamper: Any,
) -> None:
    body, headers = tamper(*signed(ringing()))

    response = await deliver(stand_in, client, body, headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook"
    assert stand_in.accepted == []
    assert await sessions_in(db_session) == []


async def test_a_recorded_request_cannot_be_replayed_later(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    ten_minutes_ago = int(time.time()) - 600

    response = await deliver(
        stand_in, client, *signed(ringing(), sent_at=ten_minutes_ago)
    )

    assert response.status_code == 400
    assert stand_in.accepted == []


async def test_a_request_signed_with_another_secret_is_refused(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    other = "whsec_" + base64.b64encode(b"somebody else's signing key....").decode()

    response = await deliver(stand_in, client, *signed(ringing(), secret=other))

    assert response.status_code == 400


async def test_the_line_is_off_without_a_webhook_secret(
    app: FastAPI, client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="postgresql+psycopg://unused",
        tax_rate=TEST_TAX_RATE,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )

    response = await deliver(stand_in, client, *signed(ringing()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "phone_line_off"


# --- Calls in progress ------------------------------------------------------


async def test_calls_in_progress_are_listed_newest_first_with_four_digits(
    client: httpx.AsyncClient, stand_in: StandInOpenAI
) -> None:
    await deliver(stand_in, client, *signed(ringing()))
    await client.post("/sessions")  # a car, not a call

    listed = (await client.get("/phone/calls")).json()

    assert len(listed) == 1
    assert listed[0]["caller_last_four"] == "0123"
    assert uuid.UUID(listed[0]["session_id"])
