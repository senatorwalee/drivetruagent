"""Session, cart and order endpoints, against a real database and the real menu.

The rules themselves are tested in test_cart.py and test_submit_order.py.
These tests check the HTTP layer: paths, status codes, the error shape, and
that each request's work is committed.
"""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import MenuItem, Option

pytestmark = pytest.mark.usefixtures("menu")

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
async def session_id(client: httpx.AsyncClient, menu: None) -> str:
    response = await client.post("/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]


async def add(
    client: httpx.AsyncClient, session_id: str, item_code: str, **body: Any
) -> httpx.Response:
    return await client.post(
        f"/sessions/{session_id}/cart/lines", json={"item_code": item_code, **body}
    )


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


# --- A whole order ----------------------------------------------------------


async def test_a_whole_order_from_greeting_to_confirmation(
    client: httpx.AsyncClient,
) -> None:
    # A car pulls up.
    opened = await client.post("/sessions", json={"lane": "lane-1"})
    assert opened.status_code == 201
    session = opened.json()
    assert (session["state"], session["language"]) == ("greeting", None)
    session_id = session["session_id"]

    # "I'd like fries." The search says a size is needed...
    matches = (await client.get("/menu/search", params={"q": "fries"})).json()
    assert (matches[0]["item_code"], matches[0]["must_ask"]) == ("fries", ["size"])

    # ...and adding without one is refused, with the sizes to offer.
    refused = await add(client, session_id, "fries")
    assert refused.status_code == 422
    assert error_code(refused) == "missing_required_options"
    missing = refused.json()["error"]["details"]["missing"]
    assert [option["option_code"] for option in missing[0]["options"]] == [
        "small",
        "medium",
        "large",
    ]

    # "Large, please."
    added = await add(client, session_id, "fries", selections={"size": ["large"]})
    assert added.status_code == 201
    assert added.json()["lines"][0]["unit_price_cents"] == 379

    # "Actually, make that two. And a medium cola."
    await client.patch(f"/sessions/{session_id}/cart/lines/1", json={"quantity": 2})
    await add(client, session_id, "cola", selections={"size": ["medium"]})

    # "And a cookie. No, forget the cookie."
    await add(client, session_id, "cookie")
    removed = await client.delete(f"/sessions/{session_id}/cart/lines/3")
    assert removed.status_code == 200

    cart = (await client.get(f"/sessions/{session_id}/cart")).json()
    assert [(line["line_no"], line["item_code"]) for line in cart["lines"]] == [
        (1, "fries"),
        (2, "cola"),
    ]
    assert (cart["subtotal_cents"], cart["tax_cents"], cart["total_cents"]) == (
        997,
        130,
        1127,
    )
    assert cart["state"] == "ordering"

    # "That's everything."
    submitted = await client.post(f"/sessions/{session_id}/order")
    assert submitted.status_code == 201
    order = submitted.json()
    assert order["total_cents"] == 1127
    assert order["lines"] == cart["lines"]

    # The order can be read back, and the session has ended.
    read_back = await client.get(f"/orders/{order['order_id']}")
    assert read_back.status_code == 200
    assert read_back.json() == order
    ended = (await client.get(f"/sessions/{session_id}")).json()
    assert (ended["state"], ended["end_reason"]) == ("submitted", "order_submitted")
    assert ended["ended_at"] is not None


# --- Sessions ---------------------------------------------------------------


async def test_open_session_without_a_body_uses_the_default_lane(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/sessions")

    assert response.status_code == 201
    assert response.json()["lane"] == "lane-1"


async def test_second_session_on_a_busy_lane_is_a_conflict(
    client: httpx.AsyncClient, session_id: str
) -> None:
    response = await client.post("/sessions")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "lane_busy",
            "message": "Lane lane-1 already has an active session.",
            "details": {"lane": "lane-1"},
        }
    }


async def test_another_lane_can_open_at_the_same_time(
    client: httpx.AsyncClient, session_id: str
) -> None:
    response = await client.post("/sessions", json={"lane": "lane-2"})

    assert response.status_code == 201


async def test_setting_the_language_makes_the_cart_french(
    client: httpx.AsyncClient, session_id: str
) -> None:
    updated = await client.patch(f"/sessions/{session_id}", json={"language": "fr"})
    cart = (
        await add(client, session_id, "fries", selections={"size": ["large"]})
    ).json()

    assert updated.status_code == 200
    assert updated.json()["language"] == "fr"
    assert cart["language"] == "fr"
    assert cart["lines"][0]["item_name"] == "Frites"
    assert cart["lines"][0]["options"][0]["option_name"] == "Grand"


async def test_french_order_reads_back_in_french(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await client.patch(f"/sessions/{session_id}", json={"language": "fr"})
    await add(client, session_id, "fries", selections={"size": ["large"]})
    order = (await client.post(f"/sessions/{session_id}/order")).json()

    read_back = (await client.get(f"/orders/{order['order_id']}")).json()

    assert read_back == order
    assert read_back["language"] == "fr"
    option = read_back["lines"][0]["options"][0]
    assert (option["group_name"], option["option_name"]) == ("Format", "Grand")


# --- Each change is committed -----------------------------------------------


async def test_a_change_is_still_there_in_the_next_request(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await add(client, session_id, "cookie", quantity=2)

    cart = (await client.get(f"/sessions/{session_id}/cart")).json()

    assert cart["version"] == 1
    assert cart["item_count"] == 2


async def test_a_refused_request_leaves_the_cart_unchanged(
    client: httpx.AsyncClient, session_id: str
) -> None:
    before = (await add(client, session_id, "cookie")).json()

    refused = await add(client, session_id, "fries", selections={"size": ["huge"]})

    after = (await client.get(f"/sessions/{session_id}/cart")).json()
    assert refused.status_code == 422
    assert after == before


async def test_changing_one_choice_keeps_the_others(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await add(
        client, session_id, "fries", selections={"size": ["small"], "style": ["curly"]}
    )

    cart = (
        await client.patch(
            f"/sessions/{session_id}/cart/lines/1",
            json={"selections": {"size": ["large"]}},
        )
    ).json()

    chosen = {o["group_code"]: o["option_code"] for o in cart["lines"][0]["options"]}
    assert chosen == {"size": "large", "style": "curly", "seasoning": "plain"}


# --- Not found: 404 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/sessions/{UNKNOWN_ID}", None),
        ("PATCH", f"/sessions/{UNKNOWN_ID}", {"language": "fr"}),
        ("GET", f"/sessions/{UNKNOWN_ID}/cart", None),
        ("POST", f"/sessions/{UNKNOWN_ID}/cart/lines", {"item_code": "cookie"}),
        ("PATCH", f"/sessions/{UNKNOWN_ID}/cart/lines/1", {"quantity": 2}),
        ("DELETE", f"/sessions/{UNKNOWN_ID}/cart/lines/1", None),
        ("POST", f"/sessions/{UNKNOWN_ID}/order", None),
    ],
)
async def test_unknown_session_is_not_found(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = await client.request(method, path, json=body)

    assert response.status_code == 404
    assert error_code(response) == "session_not_found"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_unknown_cart_line_is_not_found(
    client: httpx.AsyncClient, session_id: str, method: str
) -> None:
    await add(client, session_id, "cookie")
    body = {"quantity": 2} if method == "PATCH" else None

    response = await client.request(
        method, f"/sessions/{session_id}/cart/lines/9", json=body
    )

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "unknown_cart_line",
        "message": "The cart has no line 9.",
        "details": {"line_no": 9, "existing_lines": [1]},
    }


async def test_unknown_order_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/orders/{UNKNOWN_ID}")

    assert response.status_code == 404
    assert error_code(response) == "order_not_found"


# --- State does not allow it: 409 -------------------------------------------


async def test_empty_cart_cannot_be_submitted(
    client: httpx.AsyncClient, session_id: str
) -> None:
    response = await client.post(f"/sessions/{session_id}/order")

    assert response.status_code == 409
    assert error_code(response) == "empty_cart"


async def test_nothing_changes_after_the_order_is_submitted(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await add(client, session_id, "cookie")
    await client.post(f"/sessions/{session_id}/order")

    again = await client.post(f"/sessions/{session_id}/order")
    late_add = await add(client, session_id, "cookie")
    late_language = await client.patch(
        f"/sessions/{session_id}", json={"language": "fr"}
    )

    for response in (again, late_add, late_language):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_ended"
        assert response.json()["error"]["details"] == {"state": "submitted"}


async def test_unavailable_item_is_a_conflict(
    client: httpx.AsyncClient, session_id: str, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(is_active=False)
    )

    response = await add(client, session_id, "cookie")

    assert response.status_code == 409
    assert error_code(response) == "item_unavailable"


async def test_unavailable_option_is_a_conflict_listing_what_is_left(
    client: httpx.AsyncClient, session_id: str, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(Option).where(Option.code == "curly").values(is_active=False)
    )

    response = await add(
        client, session_id, "fries", selections={"size": ["small"], "style": ["curly"]}
    )

    assert response.status_code == 409
    assert error_code(response) == "option_unavailable"
    assert response.json()["error"]["details"]["valid_options"] == ["regular", "waffle"]


# --- The menu or the rules do not allow it: 422 -----------------------------


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ({"item_code": "lobster"}, "unknown_item"),
        ({"item_code": "fries"}, "missing_required_options"),
        ({"item_code": "cookie", "quantity": 0}, "invalid_quantity"),
        (
            {"item_code": "fries", "selections": {"size": ["huge"]}},
            "unknown_option",
        ),
        (
            {"item_code": "cookie", "selections": {"size": ["large"]}},
            "unknown_option_group",
        ),
        (
            {"item_code": "fries", "selections": {"size": ["small", "large"]}},
            "too_many_options",
        ),
    ],
)
async def test_add_refusals(
    client: httpx.AsyncClient, session_id: str, body: dict[str, Any], expected_code: str
) -> None:
    response = await client.post(f"/sessions/{session_id}/cart/lines", json=body)

    assert response.status_code == 422
    assert error_code(response) == expected_code


async def test_update_with_nothing_to_change_is_refused(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await add(client, session_id, "cookie")

    response = await client.patch(f"/sessions/{session_id}/cart/lines/1", json={})

    assert response.status_code == 422
    assert error_code(response) == "nothing_to_update"


# --- Malformed requests -----------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},  # item_code missing
        {"item_code": "cookie", "quantity": "two"},
        {"item_code": "cookie", "quantitty": 2},  # misspelt field
        {"item_code": "cookie", "selections": {"size": "large"}},  # not a list
    ],
)
async def test_malformed_body_is_rejected_before_any_rule_runs(
    client: httpx.AsyncClient, session_id: str, body: dict[str, Any]
) -> None:
    response = await client.post(f"/sessions/{session_id}/cart/lines", json=body)

    assert response.status_code == 422
    # FastAPI's own validation error, not one of ours.
    assert "detail" in response.json()
    assert "error" not in response.json()


async def test_session_id_that_is_not_a_uuid_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/sessions/not-a-uuid/cart")

    assert response.status_code == 422


async def test_unsupported_session_language_is_rejected(
    client: httpx.AsyncClient, session_id: str
) -> None:
    response = await client.patch(f"/sessions/{session_id}", json={"language": "de"})

    assert response.status_code == 422


# --- Documentation ----------------------------------------------------------


async def test_every_endpoint_is_documented_with_its_errors(
    client: httpx.AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert set(schema["paths"]) >= {
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/cart",
        "/sessions/{session_id}/cart/lines",
        "/sessions/{session_id}/cart/lines/{line_no}",
        "/sessions/{session_id}/order",
        "/orders/{order_id}",
        "/menu",
        "/menu/search",
        "/menu/items/{item_code}",
    }
    add_line = schema["paths"]["/sessions/{session_id}/cart/lines"]["post"]
    assert {"201", "404", "409", "422"} <= set(add_line["responses"])
    error_schema = add_line["responses"]["409"]["content"]["application/json"]
    assert error_schema["schema"]["$ref"].endswith("/ErrorResponse")


def test_unknown_id_is_a_valid_uuid() -> None:
    assert uuid.UUID(UNKNOWN_ID).version == 4
