"""Editing the menu through the API, against a real database and the real menu."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.prompt import build_instructions
from drivetruagent.db.models import MenuItemPhoto
from drivetruagent.seed import load_menu, photos_dir_for, seed_menu

pytestmark = pytest.mark.usefixtures("menu")

MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "menu.json"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def wrap(**overrides: Any) -> dict[str, Any]:
    return {
        "name_en": "Spicy wrap",
        "name_fr": "Wrap épicé",
        "description_en": "Crispy chicken, hot sauce and lettuce in a tortilla.",
        "category_code": "burgers",
        "base_price_cents": 699,
    } | overrides


async def menu_codes(client: httpx.AsyncClient) -> set[str]:
    categories = (await client.get("/menu")).json()["categories"]
    return {item["item_code"] for c in categories for item in c["items"]}


async def admin_item(client: httpx.AsyncClient, code: str) -> dict[str, Any]:
    items = (await client.get("/admin/menu")).json()["items"]
    return next(item for item in items if item["item_code"] == code)


# --- Reading ----------------------------------------------------------------


async def test_admin_menu_lists_every_item_with_what_the_page_shows(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get("/admin/menu")).json()

    assert [c["category_code"] for c in body["categories"]] == [
        "burgers",
        "sides",
        "drinks",
        "desserts",
    ]
    assert len(body["items"]) == 21
    fries = next(item for item in body["items"] if item["item_code"] == "fries")
    assert fries["name_en"] == "Fries"
    assert fries["name_fr"] == "Frites"
    assert fries["base_price_cents"] == 249
    assert fries["is_active"] is True
    assert fries["stock"] is not None
    assert fries["allergens_reviewed_on"] is not None
    size = next(group for group in fries["choices"] if group["group_name"] == "Size")
    assert size["required"] is True
    assert [option["option_name"] for option in size["options"]] == [
        "Small",
        "Medium",
        "Large",
    ]


# --- Adding -----------------------------------------------------------------


async def test_new_item_is_added_at_the_end_of_its_category_and_on_the_menu(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/admin/menu/items", json=wrap(stock=12))

    assert response.status_code == 201
    item = response.json()
    assert item["item_code"] == "spicy_wrap"
    assert item["stock"] == 12
    assert item["photo"] is None
    # Nobody has reviewed its allergens, so the agent will not discuss them.
    assert item["allergens_reviewed_on"] is None
    assert item["choices"] == []

    burgers = (await client.get("/menu")).json()["categories"][0]["items"]
    assert burgers[-1]["item_code"] == "spicy_wrap"
    found = (await client.get("/menu/search", params={"q": "spicy wrap"})).json()
    assert found[0]["item_code"] == "spicy_wrap"


async def test_the_next_session_tells_the_agent_about_a_new_item(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await client.post("/admin/menu/items", json=wrap())

    instructions = await build_instructions(db_session, "Test Burgers")

    assert "spicy_wrap" in instructions
    assert "Wrap épicé" in instructions


async def test_a_second_item_with_a_similar_name_gets_its_own_code(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/admin/menu/items", json=wrap())
    await client.patch("/admin/menu/items/spicy_wrap", json={"is_active": False})

    again = await client.post("/admin/menu/items", json=wrap(name_en="Spicy wrap!"))

    assert again.status_code == 201
    assert again.json()["item_code"] == "spicy_wrap_2"


async def test_a_name_already_on_the_menu_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/admin/menu/items", json=wrap(name_en="FRIES"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_item_name"
    assert response.json()["error"]["details"]["other_item_code"] == "fries"


async def test_an_unknown_category_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/admin/menu/items", json=wrap(category_code="breakfast")
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_category"


# --- Changing ---------------------------------------------------------------


async def test_only_the_fields_sent_are_changed(client: httpx.AsyncClient) -> None:
    before = await admin_item(client, "cookie")

    response = await client.patch(
        "/admin/menu/items/cookie",
        json={"base_price_cents": 199, "name_en": "Big cookie", "stock": 7},
    )

    assert response.status_code == 200
    cookie = response.json()
    assert cookie["base_price_cents"] == 199
    assert cookie["name_en"] == "Big cookie"
    assert cookie["stock"] == 7
    assert cookie["name_fr"] == before["name_fr"]
    assert cookie["description_en"] == before["description_en"]


async def test_stock_null_stops_tracking_it(client: httpx.AsyncClient) -> None:
    response = await client.patch("/admin/menu/items/fries", json={"stock": None})

    assert response.json()["stock"] is None
    stock = (await client.get("/inventory")).json()
    assert "fries" not in {level["item_code"] for level in stock}


async def test_moving_an_item_puts_it_last_in_its_new_category(
    client: httpx.AsyncClient,
) -> None:
    await client.patch("/admin/menu/items/cookie", json={"category_code": "sides"})

    sides = (await client.get("/menu")).json()["categories"][1]["items"]
    assert sides[-1]["item_code"] == "cookie"


async def test_clearing_english_clears_the_french_description_too(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch(
        "/admin/menu/items/cookie", json={"description_en": ""}
    )

    assert response.json()["description_en"] is None
    assert response.json()["description_fr"] is None


@pytest.mark.usefixtures("specials")
async def test_a_price_a_special_would_make_free_is_refused(
    client: httpx.AsyncClient,
) -> None:
    # The chicken burger special takes 150 off.
    response = await client.patch(
        "/admin/menu/items/chicken_burger", json={"base_price_cents": 150}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "price_below_special"


async def test_changing_an_unknown_item_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.patch("/admin/menu/items/nope", json={"stock": 1})

    assert response.status_code == 404


# --- Taking off and putting back --------------------------------------------


async def test_an_item_taken_off_disappears_for_guests_and_can_be_put_back(
    client: httpx.AsyncClient,
) -> None:
    off = await client.patch("/admin/menu/items/apple_pie", json={"is_active": False})

    assert off.json()["is_active"] is False
    assert "apple_pie" not in await menu_codes(client)
    found = (await client.get("/menu/search", params={"q": "apple pie"})).json()
    assert "apple_pie" not in {match["item_code"] for match in found}
    # Still listed on the menu page, so it can be put back.
    assert (await admin_item(client, "apple_pie"))["is_active"] is False

    await client.patch("/admin/menu/items/apple_pie", json={"is_active": True})

    assert "apple_pie" in await menu_codes(client)


# --- Photos -----------------------------------------------------------------


async def test_a_photo_can_be_set_read_replaced_and_removed(
    client: httpx.AsyncClient,
) -> None:
    put = await client.put(
        "/admin/menu/items/fries/photo",
        content=JPEG,
        headers={"Content-Type": "image/jpeg"},
    )

    assert put.status_code == 200
    version = put.json()["photo"]["version"]
    photo = await client.get("/menu/items/fries/photo", params={"v": version})
    assert photo.content == JPEG
    assert photo.headers["content-type"] == "image/jpeg"
    assert "immutable" in photo.headers["cache-control"]

    # The type comes from the bytes, not the header.
    replaced = await client.put(
        "/admin/menu/items/fries/photo",
        content=PNG,
        headers={"Content-Type": "image/jpeg"},
    )
    assert replaced.json()["photo"]["version"] != version
    stale = await client.get("/menu/items/fries/photo", params={"v": version})
    assert stale.headers["content-type"] == "image/png"
    assert stale.headers["cache-control"] == "no-cache"

    removed = await client.delete("/admin/menu/items/fries/photo")
    assert removed.json()["photo"] is None
    assert (await client.get("/menu/items/fries/photo")).status_code == 404


async def test_a_file_that_is_not_a_supported_image_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await client.put(
        "/admin/menu/items/fries/photo",
        content=b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        headers={"Content-Type": "image/png"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "unsupported_type"


async def test_a_photo_over_5_mb_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/admin/menu/items/fries/photo",
        content=JPEG + b"\x00" * (5 * 1024 * 1024),
        headers={"Content-Type": "image/jpeg"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["details"]["reason"] == "too_large"


async def test_a_photo_for_an_unknown_item_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    put = await client.put(
        "/admin/menu/items/nope/photo",
        content=JPEG,
        headers={"Content-Type": "image/jpeg"},
    )

    assert put.status_code == 404
    assert (await client.get("/menu/items/nope/photo")).status_code == 404


# --- Seeding photos ---------------------------------------------------------


async def test_seeding_with_photos_gives_every_item_its_photo_and_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await seed_menu(db_session, load_menu(MENU_PATH), photos_dir_for(MENU_PATH))
    await seed_menu(db_session, load_menu(MENU_PATH), photos_dir_for(MENU_PATH))

    count = await db_session.scalar(select(func.count()).select_from(MenuItemPhoto))
    assert count == 21
    fries = await admin_item(client, "fries")
    assert fries["photo"]["credit"].endswith("on Unsplash")
    photo = await client.get("/menu/items/fries/photo")
    assert photo.content[:3] == b"\xff\xd8\xff"
