"""The conversation endpoint, with a scripted stand-in for the model."""

import httpx
import pytest
from fastapi import FastAPI

from drivetruagent.agent.scripted import ScriptedModel, call, say
from drivetruagent.api.conversation import get_model_client

pytestmark = pytest.mark.usefixtures("menu")

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"
LARGE = [{"group": "size", "options": ["large"]}]


@pytest.fixture
async def session_id(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


def use_model(app: FastAPI, model: ScriptedModel) -> None:
    app.dependency_overrides[get_model_client] = lambda: model


async def test_a_message_gets_a_reply_and_the_cart(
    app: FastAPI, client: httpx.AsyncClient, session_id: str
) -> None:
    use_model(
        app,
        ScriptedModel(
            [
                call("add_item", item_code="fries", quantity=1, selections=LARGE),
                say("One large fries. Anything else?"),
            ]
        ),
    )

    response = await client.post(
        f"/sessions/{session_id}/messages", json={"text": "A large fries please"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "One large fries. Anything else?"
    assert body["tools_used"] == ["add_item"]
    assert body["cart"]["lines"][0]["item_code"] == "fries"
    assert body["cart"]["total_cents"] == 428


async def test_the_agents_changes_are_visible_to_the_other_endpoints(
    app: FastAPI, client: httpx.AsyncClient, session_id: str
) -> None:
    """The order screen reads the same cart the agent just changed."""
    use_model(
        app,
        ScriptedModel(
            [
                call("add_item", item_code="cookie", quantity=2, selections=[]),
                say("Two cookies."),
            ]
        ),
    )
    await client.post(f"/sessions/{session_id}/messages", json={"text": "Two cookies"})

    cart = (await client.get(f"/sessions/{session_id}/cart")).json()

    assert cart["item_count"] == 2
    assert cart["state"] == "ordering"


async def test_a_conversation_continues_across_requests(
    app: FastAPI, client: httpx.AsyncClient, session_id: str
) -> None:
    first = ScriptedModel([say("Welcome! What can I get you?")])
    use_model(app, first)
    await client.post(f"/sessions/{session_id}/messages", json={"text": "Hi"})
    second = ScriptedModel([say("Sure.")])
    use_model(app, second)

    await client.post(f"/sessions/{session_id}/messages", json={"text": "A cookie"})

    # The second request's model was shown the first exchange as well.
    assert len(second.requests[0].messages) == 3


async def test_without_a_model_the_agent_is_unavailable_but_ordering_still_works(
    client: httpx.AsyncClient, session_id: str
) -> None:
    conversation = await client.post(
        f"/sessions/{session_id}/messages", json={"text": "Hi"}
    )
    ordering = await client.post(
        f"/sessions/{session_id}/cart/lines", json={"item_code": "cookie"}
    )

    assert conversation.status_code == 503
    assert conversation.json()["error"]["code"] == "agent_unavailable"
    assert "OPENAI_API_KEY" in conversation.json()["error"]["message"]
    assert ordering.status_code == 201


async def test_unknown_session_is_not_found(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_model(app, ScriptedModel([say("Hello!")]))

    response = await client.post(
        f"/sessions/{UNKNOWN_ID}/messages", json={"text": "Hi"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize(
    "body", [{}, {"text": ""}, {"text": "x" * 2001}, {"message": "Hi"}]
)
async def test_a_malformed_message_is_rejected(
    app: FastAPI, client: httpx.AsyncClient, session_id: str, body: dict[str, str]
) -> None:
    model = ScriptedModel([say("Hello!")])
    use_model(app, model)

    response = await client.post(f"/sessions/{session_id}/messages", json=body)

    assert response.status_code == 422
    # The model was never asked.
    assert model.requests == []


# --- Agent info -------------------------------------------------------------


async def test_agent_info_says_when_no_model_is_configured(
    client: httpx.AsyncClient,
) -> None:
    info = (await client.get("/agent")).json()

    assert info == {
        "restaurant_name": "Maple Lane Burgers",
        "available": False,
        "model": None,
        "scripted": False,
        "suggested_lines": [],
    }


# --- Events -----------------------------------------------------------------


async def test_events_list_everything_that_happened_in_order(
    app: FastAPI, client: httpx.AsyncClient, session_id: str
) -> None:
    use_model(
        app,
        ScriptedModel(
            [
                call("add_item", item_code="fries", quantity=1, selections=LARGE),
                say("One large fries. Anything else?"),
            ]
        ),
    )
    await client.post(
        f"/sessions/{session_id}/messages", json={"text": "A large fries please"}
    )

    response = await client.get(f"/sessions/{session_id}/events")

    assert response.status_code == 200
    log = response.json()
    assert [event["kind"] for event in log] == [
        "user_message",
        "model_call",
        "tool_call",
        "tool_result",
        "model_call",
        "assistant_message",
    ]
    assert [event["seq"] for event in log] == sorted(event["seq"] for event in log)
    assert len({event["turn_id"] for event in log}) == 1
    assert log[0]["payload"] == {"text": "A large fries please"}
    # A tool result carries the decision and the cart as it stood.
    result = log[3]["payload"]["result"]
    assert result["decision"] == "allow"
    assert result["cart"]["lines"][0]["item_code"] == "fries"


async def test_events_after_a_sequence_number_returns_only_newer_ones(
    app: FastAPI, client: httpx.AsyncClient, session_id: str
) -> None:
    use_model(app, ScriptedModel([say("Welcome!"), say("Sure.")]))
    await client.post(f"/sessions/{session_id}/messages", json={"text": "Hi"})
    seen = (await client.get(f"/sessions/{session_id}/events")).json()
    await client.post(f"/sessions/{session_id}/messages", json={"text": "A cookie"})

    newer = (
        await client.get(
            f"/sessions/{session_id}/events", params={"after": seen[-1]["seq"]}
        )
    ).json()

    assert [event["kind"] for event in newer] == [
        "user_message",
        "model_call",
        "assistant_message",
    ]
    assert newer[0]["payload"] == {"text": "A cookie"}
    assert all(event["seq"] > seen[-1]["seq"] for event in newer)


async def test_a_new_session_has_no_events(
    client: httpx.AsyncClient, session_id: str
) -> None:
    assert (await client.get(f"/sessions/{session_id}/events")).json() == []


async def test_events_for_an_unknown_session_are_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/sessions/{UNKNOWN_ID}/events")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
