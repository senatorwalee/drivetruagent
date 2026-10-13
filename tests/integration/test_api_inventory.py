"""Stock over HTTP: the staff endpoints, and what a customer-facing call sees."""

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("menu")


@pytest.fixture
async def session_id(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


async def stock_of(client: httpx.AsyncClient, item_code: str) -> dict[str, object]:
    levels = (await client.get("/inventory")).json()
    return next(level for level in levels if level["item_code"] == item_code)


async def set_stock(client: httpx.AsyncClient, item_code: str, on_hand: int) -> None:
    response = await client.put(f"/inventory/{item_code}", json={"on_hand": on_hand})
    assert response.status_code == 200, response.text


# --- Staff endpoints --------------------------------------------------------


async def test_stock_levels_list_every_tracked_item(client: httpx.AsyncClient) -> None:
    response = await client.get("/inventory")

    assert response.status_code == 200
    levels = response.json()
    assert len(levels) == 20
    assert [level["item_code"] for level in levels] == sorted(
        level["item_code"] for level in levels
    )
    assert await stock_of(client, "cookie") == {
        "item_code": "cookie",
        "on_hand": 100,
        "reserved": 0,
        "available": 100,
    }


async def test_setting_stock_is_saved(client: httpx.AsyncClient) -> None:
    response = await client.put("/inventory/cookie", json={"on_hand": 7})

    assert response.status_code == 200
    assert response.json() == {
        "item_code": "cookie",
        "on_hand": 7,
        "reserved": 0,
        "available": 7,
    }
    assert (await stock_of(client, "cookie"))["on_hand"] == 7


async def test_stock_levels_show_what_sessions_are_holding(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await set_stock(client, "cookie", 5)

    await client.post(
        f"/sessions/{session_id}/cart/lines",
        json={"item_code": "cookie", "quantity": 2},
    )

    assert await stock_of(client, "cookie") == {
        "item_code": "cookie",
        "on_hand": 5,
        "reserved": 2,
        "available": 3,
    }


async def test_setting_stock_for_an_unknown_item_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put("/inventory/lobster", json={"on_hand": 5})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_item"


@pytest.mark.parametrize(
    "body", [{"on_hand": -1}, {"on_hand": "lots"}, {}, {"count": 5}]
)
async def test_a_bad_stock_value_is_rejected(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    response = await client.put("/inventory/cookie", json=body)

    assert response.status_code == 422
    assert (await stock_of(client, "cookie"))["on_hand"] == 100


# --- What ordering sees -----------------------------------------------------


async def test_running_out_is_a_conflict_with_what_is_left_and_what_to_offer(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await set_stock(client, "cookie", 1)

    response = await client.post(
        f"/sessions/{session_id}/cart/lines",
        json={"item_code": "cookie", "quantity": 2},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert error["details"]["requested"] == 2
    assert error["details"]["available"] == 1
    assert [a["item_code"] for a in error["details"]["alternatives"]] == [
        "apple_pie",
        "sundae",
        "soft_serve_cone",
    ]


async def test_the_agent_endpoint_reports_it_as_a_rejection(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await set_stock(client, "cookie", 0)

    response = await client.post(
        f"/sessions/{session_id}/actions",
        json={"type": "add_item", "item_code": "cookie"},
    )

    assert response.status_code == 200
    outcome = response.json()
    assert (outcome["decision"], outcome["rule"]) == ("reject", "insufficient_stock")
    assert outcome["error"]["details"]["available"] == 0
    assert outcome["cart"]["lines"] == []


async def test_the_menu_says_what_is_sold_out(client: httpx.AsyncClient) -> None:
    await set_stock(client, "cookie", 0)

    menu = (await client.get("/menu")).json()
    search = (await client.get("/menu/search", params={"q": "cookie"})).json()
    details = (await client.get("/menu/items/cookie")).json()

    items = {item["item_code"]: item for c in menu["categories"] for item in c["items"]}
    assert items["cookie"]["in_stock"] is False
    assert items["apple_pie"]["in_stock"] is True
    assert search[0]["in_stock"] is False
    assert details["in_stock"] is False


async def test_a_placed_order_lowers_the_stock(
    client: httpx.AsyncClient, session_id: str
) -> None:
    await set_stock(client, "cookie", 5)
    await client.post(
        f"/sessions/{session_id}/cart/lines",
        json={"item_code": "cookie", "quantity": 2},
    )

    await client.post(f"/sessions/{session_id}/order")

    assert await stock_of(client, "cookie") == {
        "item_code": "cookie",
        "on_hand": 3,
        "reserved": 0,
        "available": 3,
    }
