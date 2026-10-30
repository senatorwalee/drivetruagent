"""Editing the menu through the API, against a real database and the real menu."""

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.prompt import build_instructions
from drivetruagent.api.deps import get_business_date
from drivetruagent.db.models import (
    AllergenDeclaration,
    MenuItem,
    MenuItemPhoto,
    Option,
    OptionGroup,
)
from drivetruagent.enums import Allergen, Language
from drivetruagent.ordering import allergen_matrix
from drivetruagent.ordering.cart import add_item, get_cart
from drivetruagent.ordering.errors import OptionUnavailable
from drivetruagent.ordering.menu import get_item_details
from drivetruagent.ordering.sessions import open_session
from drivetruagent.seed import load_menu, photos_dir_for, seed_menu

from .conftest import TEST_TAX_RATE

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
    assert fries["sizes"] == [
        {"name_en": "Small", "price_cents": 249, "is_default": False},
        {"name_en": "Medium", "price_cents": 319, "is_default": False},
        {"name_en": "Large", "price_cents": 379, "is_default": False},
    ]
    # Sizes are edited; other choices are shown as they are.
    assert [group["group_name"] for group in fries["choices"]] == [
        "Style",
        "Seasoning",
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


# --- Allergens --------------------------------------------------------------

SESAME_MAYBE = {"allergen": "sesame", "because": "Shared with the buns."}


async def test_a_new_item_can_be_reviewed_as_it_is_added(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/admin/menu/items",
        json=wrap(allergens={"contains": ["wheat"], "may_contain": [SESAME_MAYBE]}),
    )

    assert response.status_code == 201
    item = response.json()
    assert item["allergens_reviewed_on"] is not None
    assert item["allergens"] == {
        "contains": ["wheat"],
        "may_contain": [SESAME_MAYBE],
    }


async def test_a_new_item_with_no_review_is_not_reviewed(
    client: httpx.AsyncClient,
) -> None:
    item = (await client.post("/admin/menu/items", json=wrap())).json()

    assert item["allergens_reviewed_on"] is None
    assert item["allergens"] is None


async def test_changing_a_review_replaces_what_the_item_declared(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(
        "/admin/menu/items/fries", json={"allergens": {"contains": ["mustard"]}}
    )

    assert response.status_code == 200
    assert response.json()["allergens"] == {"contains": ["mustard"], "may_contain": []}
    report = await allergen_matrix.item_allergens(
        db_session, "fries", None, None, Language.EN
    )
    assert [found.allergen for found in report.contains] == [Allergen.MUSTARD]


async def test_a_review_is_dated_today_where_the_restaurant_is(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.dependency_overrides[get_business_date] = lambda: date(2026, 11, 3)

    response = await client.patch(
        "/admin/menu/items/fries", json={"allergens": {"none_of_these": True}}
    )

    assert response.json()["allergens_reviewed_on"] == "2026-11-03"


async def test_an_edit_that_leaves_allergens_out_leaves_the_review_alone(
    client: httpx.AsyncClient,
) -> None:
    before = await admin_item(client, "fries")

    await client.patch("/admin/menu/items/fries", json={"name_fr": "Frites maison"})

    after = await admin_item(client, "fries")
    assert after["allergens"] == before["allergens"]
    assert after["allergens_reviewed_on"] == before["allergens_reviewed_on"]


async def test_an_unreviewed_item_stays_unreviewed_through_other_edits(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/admin/menu/items", json=wrap())

    response = await client.patch(
        "/admin/menu/items/spicy_wrap", json={"base_price_cents": 749}
    )

    assert response.json()["allergens_reviewed_on"] is None


async def test_a_review_can_be_taken_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch("/admin/menu/items/fries", json={"allergens": None})

    assert response.json()["allergens_reviewed_on"] is None
    assert response.json()["allergens"] is None
    report = await allergen_matrix.item_allergens(
        db_session, "fries", None, None, Language.EN
    )
    assert report.verified is False


@pytest.mark.parametrize(
    "allergens",
    [
        {},
        {"contains": [], "may_contain": []},
        {"none_of_these": True, "contains": ["milk"]},
        {"may_contain": [{"allergen": "milk", "because": " "}]},
    ],
)
async def test_a_review_that_does_not_say_what_it_means_is_refused(
    client: httpx.AsyncClient, allergens: dict[str, Any]
) -> None:
    before = await admin_item(client, "fries")

    response = await client.patch(
        "/admin/menu/items/fries", json={"allergens": allergens}
    )

    assert response.status_code == 422
    assert await admin_item(client, "fries") == before


async def test_what_a_choice_adds_is_shown_and_survives_a_new_review(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Choices come from the menu file. Reviewing the item itself leaves them."""
    before = await admin_item(client, "classic_burger")
    assert {"option_name": "Cheese", "contains": ["milk"], "may_contain": []} in before[
        "option_allergens"
    ]

    await client.patch(
        "/admin/menu/items/classic_burger", json={"allergens": {"contains": ["wheat"]}}
    )

    report = await allergen_matrix.item_allergens(
        db_session, "classic_burger", {"extras": ["cheese"]}, None, Language.EN
    )
    assert {found.allergen for found in report.contains} == {
        Allergen.WHEAT,
        Allergen.MILK,
    }


async def test_a_french_reason_is_kept_while_its_english_is_unchanged(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    burger = await admin_item(client, "classic_burger")
    kept = burger["allergens"]["may_contain"][0]
    assert kept["because"]
    french_before = await db_session.scalar(
        select(AllergenDeclaration.note_i18n)
        .join(MenuItem, MenuItem.id == AllergenDeclaration.item_id)
        .where(
            MenuItem.code == "classic_burger",
            AllergenDeclaration.option_id.is_(None),
            AllergenDeclaration.allergen == kept["allergen"],
        )
    )
    assert french_before is not None
    assert "fr" in french_before

    response = await client.patch(
        "/admin/menu/items/classic_burger",
        json={"allergens": {"contains": ["mustard"], "may_contain": [kept]}},
    )

    assert response.status_code == 200
    report = await allergen_matrix.item_allergens(
        db_session, "classic_burger", None, None, Language.FR
    )
    assert report.may_contain[0].because == [french_before["fr"]]


async def test_a_reason_rewritten_in_english_drops_its_old_french(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    burger = await admin_item(client, "classic_burger")
    kept = burger["allergens"]["may_contain"][0]

    await client.patch(
        "/admin/menu/items/classic_burger",
        json={
            "allergens": {
                "may_contain": [{"allergen": kept["allergen"], "because": "New oil."}]
            }
        },
    )

    report = await allergen_matrix.item_allergens(
        db_session, "classic_burger", None, None, Language.FR
    )
    assert report.may_contain[0].because == ["New oil."]


# --- Sizes ------------------------------------------------------------------

COLA_SIZES = [
    {"name_en": "Small", "price_cents": 199},
    {"name_en": "Large", "price_cents": 279},
]


async def cola_options(db_session: AsyncSession) -> dict[str, Option]:
    rows = await db_session.execute(
        select(Option)
        .join(OptionGroup, Option.option_group_id == OptionGroup.id)
        .join(MenuItem, OptionGroup.item_id == MenuItem.id)
        .where(MenuItem.code == "cola")
        .execution_options(populate_existing=True)
    )
    return {option.code: option for option in rows.scalars()}


async def add_cola(db_session: AsyncSession, size: str) -> uuid.UUID:
    customer_session = await open_session(db_session, lane="1")
    await add_item(
        db_session,
        customer_session.id,
        "cola",
        1,
        {"size": [size]},
        tax_rate=TEST_TAX_RATE,
    )
    return customer_session.id


async def test_a_new_item_can_come_in_sizes(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/admin/menu/items",
        json=wrap(
            base_price_cents=699,
            sizes=[
                {"name_en": "Regular", "price_cents": 699, "is_default": True},
                {"name_en": "Large", "price_cents": 899},
            ],
        ),
    )

    assert response.status_code == 201
    assert response.json()["base_price_cents"] == 699
    assert response.json()["sizes"] == [
        {"name_en": "Regular", "price_cents": 699, "is_default": True},
        {"name_en": "Large", "price_cents": 899, "is_default": False},
    ]


async def test_sizes_can_be_added_to_an_item_that_had_none(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(
        "/admin/menu/items/classic_burger",
        json={
            "sizes": [
                {"name_en": "Single", "price_cents": 549},
                {"name_en": "Double", "price_cents": 799},
            ]
        },
    )

    assert response.status_code == 200
    details = await get_item_details(db_session, "classic_burger", Language.EN)
    assert "size" in details.must_ask
    # Its other choices are still there, and come first.
    assert [group.group_code for group in details.choices] == ["extras", "size"]


async def test_new_prices_keep_the_rule_that_the_cheapest_is_the_base(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(
        "/admin/menu/items/cola",
        json={
            "sizes": [
                {"name_en": "Small", "price_cents": 229},
                {"name_en": "Medium", "price_cents": 269},
                {"name_en": "Large", "price_cents": 319},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["base_price_cents"] == 229
    options = await cola_options(db_session)
    assert options["large"].price_delta_cents == 90


async def test_a_removed_size_is_deactivated_not_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    session_id = await add_cola(db_session, "medium")

    response = await client.patch("/admin/menu/items/cola", json={"sizes": COLA_SIZES})

    assert response.status_code == 200
    assert [size["name_en"] for size in response.json()["sizes"]] == ["Small", "Large"]
    options = await cola_options(db_session)
    assert options["medium"].is_active is False
    # The car already ordering it keeps it, at the price it was given.
    cart = await get_cart(db_session, session_id, tax_rate=TEST_TAX_RATE)
    assert cart.lines[0].options[0].option_code == "medium"
    assert cart.lines[0].unit_price_cents == 239


async def test_a_removed_size_is_no_longer_offered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch("/admin/menu/items/cola", json={"sizes": COLA_SIZES})

    details = await get_item_details(db_session, "cola", Language.EN)
    assert [option.option_code for option in details.choices[0].options] == [
        "small",
        "large",
    ]
    customer_session = await open_session(db_session, lane="1")
    with pytest.raises(OptionUnavailable):
        await add_item(
            db_session,
            customer_session.id,
            "cola",
            1,
            {"size": ["medium"]},
            tax_rate=TEST_TAX_RATE,
        )


async def test_removing_the_cheapest_size_moves_the_price_up(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Large is still 2.79. A size can't cost less than the item's price, so a
    Small already in a cart stays there at the new lowest price."""
    session_id = await add_cola(db_session, "small")

    response = await client.patch(
        "/admin/menu/items/cola",
        json={
            "sizes": [
                {"name_en": "Medium", "price_cents": 239},
                {"name_en": "Large", "price_cents": 279},
            ]
        },
    )

    assert response.json()["base_price_cents"] == 239
    assert [size["price_cents"] for size in response.json()["sizes"]] == [239, 279]
    cart = await get_cart(db_session, session_id, tax_rate=TEST_TAX_RATE)
    assert cart.lines[0].options[0].option_code == "small"
    assert cart.lines[0].unit_price_cents == 239


async def test_a_removed_size_added_back_is_the_same_size(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    before = await cola_options(db_session)
    await client.patch("/admin/menu/items/cola", json={"sizes": COLA_SIZES})

    response = await client.patch(
        "/admin/menu/items/cola",
        json={
            "sizes": [
                COLA_SIZES[0],
                {"name_en": "Medium", "price_cents": 249},
                COLA_SIZES[1],
            ]
        },
    )

    assert response.status_code == 200
    after = await cola_options(db_session)
    assert set(after) == set(before)
    assert after["medium"].is_active is True
    assert after["medium"].price_delta_cents == 50
    # Its French name came back with it.
    assert after["medium"].name_i18n["fr"] == "Moyen"


async def test_the_default_can_move_and_a_removed_one_goes_with_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    small, large = COLA_SIZES
    await client.patch(
        "/admin/menu/items/cola",
        json={"sizes": [{**small, "is_default": True}, large]},
    )

    moved = await client.patch(
        "/admin/menu/items/cola",
        json={"sizes": [small, {**large, "is_default": True}]},
    )
    assert moved.status_code == 200
    assert [size["is_default"] for size in moved.json()["sizes"]] == [False, True]

    await client.patch(
        "/admin/menu/items/cola",
        json={"sizes": [small, {"name_en": "Medium", "price_cents": 239}]},
    )
    assert (await get_item_details(db_session, "cola", Language.EN)).must_ask == [
        "size"
    ]
    assert not (await cola_options(db_session))["large"].is_default


async def test_taking_sizes_off_leaves_an_item_that_can_still_be_ordered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The group stays, because carts point at its options. It must stop
    being required, or nobody could order the item again."""
    session_id = await add_cola(db_session, "large")

    response = await client.patch(
        "/admin/menu/items/cola", json={"sizes": None, "base_price_cents": 229}
    )

    assert response.status_code == 200
    assert response.json()["sizes"] == []
    assert response.json()["base_price_cents"] == 229
    details = await get_item_details(db_session, "cola", Language.EN)
    assert details.choices == []
    assert details.must_ask == []
    assert all(
        not option.is_active for option in (await cola_options(db_session)).values()
    )
    cart = await get_cart(db_session, session_id, tax_rate=TEST_TAX_RATE)
    assert cart.lines[0].options[0].option_code == "large"
    await add_item(
        db_session,
        (await open_session(db_session, lane="2")).id,
        "cola",
        1,
        tax_rate=TEST_TAX_RATE,
    )


async def test_sizes_taken_off_can_come_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await client.patch("/admin/menu/items/cola", json={"sizes": None})

    response = await client.patch("/admin/menu/items/cola", json={"sizes": COLA_SIZES})

    assert response.status_code == 200
    assert (await get_item_details(db_session, "cola", Language.EN)).must_ask == [
        "size"
    ]


async def test_an_edit_that_leaves_sizes_out_leaves_them_alone(
    client: httpx.AsyncClient,
) -> None:
    before = await admin_item(client, "cola")

    await client.patch("/admin/menu/items/cola", json={"name_fr": "Cola maison"})

    assert (await admin_item(client, "cola"))["sizes"] == before["sizes"]


@pytest.mark.usefixtures("specials")
async def test_a_size_a_special_would_make_free_is_refused(
    client: httpx.AsyncClient,
) -> None:
    # The onion ring special takes 100 off.
    response = await client.patch(
        "/admin/menu/items/onion_rings",
        json={
            "sizes": [
                {"name_en": "Regular", "price_cents": 90},
                {"name_en": "Large", "price_cents": 200},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "price_below_special"


async def test_a_price_that_disagrees_with_the_sizes_is_refused(
    client: httpx.AsyncClient,
) -> None:
    before = await admin_item(client, "cola")

    response = await client.patch(
        "/admin/menu/items/cola",
        json={"sizes": COLA_SIZES, "base_price_cents": 999},
    )

    assert response.status_code == 422
    assert await admin_item(client, "cola") == before
