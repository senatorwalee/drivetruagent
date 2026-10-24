"""Menu endpoints, against a real database and the real menu."""

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import MenuItem, Option

pytestmark = pytest.mark.usefixtures("menu")


# --- Whole menu -------------------------------------------------------------


async def test_menu_lists_every_item_by_category_in_display_order(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/menu")

    assert response.status_code == 200
    categories = response.json()["categories"]
    assert [category["category_code"] for category in categories] == [
        "burgers",
        "sides",
        "drinks",
        "desserts",
    ]
    assert sum(len(category["items"]) for category in categories) == 21
    assert [item["item_code"] for item in categories[1]["items"]] == [
        "fries",
        "onion_rings",
        "chicken_nuggets",
        "side_salad",
    ]


async def test_menu_says_which_choices_must_be_asked(
    client: httpx.AsyncClient,
) -> None:
    categories = (await client.get("/menu")).json()["categories"]
    items = {item["item_code"]: item for c in categories for item in c["items"]}

    assert items["fries"]["must_ask"] == ["size"]
    assert items["milkshake"]["must_ask"] == ["size", "flavour"]
    assert items["cookie"]["must_ask"] == []
    assert items["fries"]["base_price_cents"] == 249


async def test_menu_in_french(client: httpx.AsyncClient) -> None:
    categories = (await client.get("/menu", params={"language": "fr"})).json()[
        "categories"
    ]

    assert categories[1]["category_name"] == "Accompagnements"
    assert categories[1]["items"][0]["item_name"] == "Frites"
    # Codes never change with language.
    assert categories[1]["items"][0]["item_code"] == "fries"


async def test_inactive_item_is_left_out_of_the_menu(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(is_active=False)
    )

    categories = (await client.get("/menu")).json()["categories"]

    codes = {item["item_code"] for c in categories for item in c["items"]}
    assert "cookie" not in codes
    assert len(codes) == 20


async def test_unsupported_language_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/menu", params={"language": "de"})

    assert response.status_code == 422


# --- Search -----------------------------------------------------------------


async def test_search_finds_an_item_and_says_what_to_ask(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/menu/search", params={"q": "large fries"})

    assert response.status_code == 200
    best = response.json()[0]
    assert best["item_code"] == "fries"
    assert best["item_name"] == "Fries"
    assert best["category_code"] == "sides"
    assert best["must_ask"] == ["size"]
    assert best["score"] > 80


async def test_search_in_french_returns_french_names(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/menu/search", params={"q": "thé glacé", "language": "fr"}
    )

    best = response.json()[0]
    assert best["item_code"] == "iced_tea"
    assert best["item_name"] == "Thé glacé"


async def test_french_words_are_found_in_an_english_session(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/menu/search", params={"q": "frites"})

    best = response.json()[0]
    assert (best["item_code"], best["item_name"]) == ("fries", "Fries")


async def test_vague_search_returns_several_equal_matches(
    client: httpx.AsyncClient,
) -> None:
    matches = (await client.get("/menu/search", params={"q": "burger"})).json()

    assert len(matches) == 5
    assert {match["score"] for match in matches} == {100}


async def test_search_for_something_not_on_the_menu_returns_an_empty_list(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/menu/search", params={"q": "lobster"})

    assert response.status_code == 200
    assert response.json() == []


async def test_search_leaves_out_inactive_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cola").values(is_active=False)
    )

    matches = (await client.get("/menu/search", params={"q": "coke"})).json()

    assert matches == []


async def test_search_respects_the_limit(client: httpx.AsyncClient) -> None:
    matches = (
        await client.get("/menu/search", params={"q": "burger", "limit": 2})
    ).json()

    assert len(matches) == 2


@pytest.mark.parametrize(
    "params", [{}, {"q": ""}, {"q": "fries", "limit": 0}, {"q": "fries", "limit": 99}]
)
async def test_bad_search_parameters_are_rejected(
    client: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await client.get("/menu/search", params=params)

    assert response.status_code == 422


# --- One item ---------------------------------------------------------------


async def test_item_details_describe_every_choice(client: httpx.AsyncClient) -> None:
    response = await client.get("/menu/items/fries")

    assert response.status_code == 200
    fries = response.json()
    assert fries["item_code"] == "fries"
    assert fries["available"] is True
    assert fries["must_ask"] == ["size"]
    size, style, seasoning = fries["choices"]
    assert size == {
        "group_code": "size",
        "group_name": "Size",
        "min_select": 1,
        "max_select": 1,
        "must_ask": True,
        "default_option_code": None,
        "options": [
            {"option_code": "small", "option_name": "Small", "price_delta_cents": 0},
            {"option_code": "medium", "option_name": "Medium", "price_delta_cents": 70},
            {"option_code": "large", "option_name": "Large", "price_delta_cents": 130},
        ],
    }
    assert (style["must_ask"], style["default_option_code"]) == (False, "regular")
    assert (seasoning["must_ask"], seasoning["default_option_code"]) == (False, "plain")


async def test_optional_choice_is_not_asked_and_has_no_default(
    client: httpx.AsyncClient,
) -> None:
    nuggets = (await client.get("/menu/items/chicken_nuggets")).json()

    dip = nuggets["choices"][1]
    assert (dip["group_code"], dip["min_select"], dip["max_select"]) == ("dip", 0, 2)
    assert dip["must_ask"] is False
    assert dip["default_option_code"] is None


async def test_item_details_in_french(client: httpx.AsyncClient) -> None:
    fries = (await client.get("/menu/items/fries", params={"language": "fr"})).json()

    assert fries["item_name"] == "Frites"
    assert fries["choices"][0]["group_name"] == "Format"
    assert fries["choices"][0]["options"][2]["option_name"] == "Grand"


async def test_item_details_include_the_description(client: httpx.AsyncClient) -> None:
    burger = (await client.get("/menu/items/classic_burger")).json()
    cookie = (await client.get("/menu/items/cookie")).json()

    assert burger["description"].startswith("Beef patty")
    assert cookie["description"] is None
    assert cookie["choices"] == []


async def test_inactive_option_is_not_offered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(Option).where(Option.code == "waffle").values(is_active=False)
    )

    fries = (await client.get("/menu/items/fries")).json()

    style_options = [o["option_code"] for o in fries["choices"][1]["options"]]
    assert style_options == ["regular", "curly"]


async def test_inactive_item_is_reported_as_unavailable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(MenuItem).where(MenuItem.code == "cookie").values(is_active=False)
    )

    response = await client.get("/menu/items/cookie")

    assert response.status_code == 200
    assert response.json()["available"] is False


async def test_unknown_item_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/menu/items/lobster")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "unknown_item",
            "message": "There is no menu item with code 'lobster'.",
            "details": {"item_code": "lobster"},
        }
    }
