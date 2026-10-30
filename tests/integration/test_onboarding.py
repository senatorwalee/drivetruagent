"""Going live through the API, against a real database and the real menu."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.prompt import build_instructions
from drivetruagent.db.models import (
    ComboRule,
    DietSuitability,
    MenuCategory,
    MenuItem,
    RestaurantProfile,
)
from drivetruagent.enums import Allergen, Channel, Diet, DietStatus, Language
from drivetruagent.ordering import allergen_matrix, inventory
from drivetruagent.ordering import diet as diet_rules
from drivetruagent.ordering.cart import add_item, get_cart
from drivetruagent.ordering.errors import MissingRequiredOptions
from drivetruagent.ordering.menu import get_item_details
from drivetruagent.ordering.sessions import open_session
from drivetruagent.seed import load_menu, seed_menu

from .conftest import MENU_PATH, TEST_TAX_RATE

pytestmark = pytest.mark.usefixtures("menu")


def setup(**overrides: Any) -> dict[str, Any]:
    return {
        "restaurant": {
            "name": "Harbour Grill",
            "address": "12 Water Street, Kingston",
            "pickup_minutes": 25,
        },
        "items": [
            item("Fish taco", "Tacos", 650),
            item("Slaw cup", "Extras", 300, stock=40),
        ],
    } | overrides


def item(name: str, section: str, price: int, **overrides: Any) -> dict[str, Any]:
    return {
        "name_en": name,
        "section": section,
        "base_price_cents": price,
    } | overrides


async def on_menu(client: httpx.AsyncClient) -> set[str]:
    categories = (await client.get("/menu")).json()["categories"]
    return {entry["item_name"] for c in categories for entry in c["items"]}


async def go_live(client: httpx.AsyncClient, **overrides: Any) -> httpx.Response:
    return await client.post("/admin/onboarding", json=setup(**overrides))


# --- Adding to the menu -----------------------------------------------------


async def test_going_live_adds_the_items_and_leaves_the_menu_alone(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(client)

    assert response.status_code == 201
    assert response.json() == {
        "restaurant_name": "Harbour Grill",
        "items": 2,
        "sections_added": 2,
        "combos": 0,
        "diet_reviews": 0,
        "allergen_reviews": 0,
        "items_taken_off": 0,
        "combos_retired": 0,
    }
    assert {"Fish taco", "Slaw cup", "Classic burger"} <= await on_menu(client)


async def test_a_section_with_the_same_name_is_used_not_made_again(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(client, items=[item("Fish taco", "BURGERS", 650)])

    assert response.json()["sections_added"] == 0
    admin = (await client.get("/admin/menu")).json()
    assert [c["category_code"] for c in admin["categories"]].count("burgers") == 1
    taco = next(i for i in admin["items"] if i["name_en"] == "Fish taco")
    assert taco["category_code"] == "burgers"


async def test_a_new_section_goes_at_the_end_of_the_menu(
    client: httpx.AsyncClient,
) -> None:
    await go_live(client)

    admin = (await client.get("/admin/menu")).json()
    assert [c["category_code"] for c in admin["categories"]][-2:] == ["tacos", "extras"]


async def test_quantity_is_unlimited_unless_a_count_is_given(
    client: httpx.AsyncClient,
) -> None:
    await go_live(client)

    items = {i["name_en"]: i for i in (await client.get("/admin/menu")).json()["items"]}
    assert items["Fish taco"]["stock"] is None
    assert items["Slaw cup"]["stock"] == 40


async def test_a_new_item_starts_with_its_allergens_unreviewed(
    client: httpx.AsyncClient,
) -> None:
    """Nothing here says what is in the food, so nothing may be claimed."""
    await go_live(client)

    items = {i["name_en"]: i for i in (await client.get("/admin/menu")).json()["items"]}
    assert items["Fish taco"]["allergens_reviewed_on"] is None


# --- What the restaurant registered -----------------------------------------


async def test_the_agent_uses_what_the_restaurant_registered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client)

    assert (await client.get("/agent")).json()["restaurant_name"] == "Harbour Grill"
    phone = await build_instructions(
        db_session, "From the settings", channel=Channel.PHONE, pickup_minutes=15
    )
    assert phone.startswith("You take phone orders for pickup at Harbour Grill")
    assert "ready in about 25 minutes" in phone
    assert "it is at 12 Water Street, Kingston." in phone
    assert "fish_taco" in phone


async def test_a_car_is_not_told_the_pickup_address(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client)

    instructions = await build_instructions(db_session, "From the settings")

    assert instructions.startswith("You take orders at the drive-through of Harbour")
    assert "12 Water Street" not in instructions


async def test_before_registering_the_settings_stand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.get("/agent")).json()["restaurant_name"] == (
        "Maple Lane Burgers"
    )
    instructions = await build_instructions(db_session, "From the settings")
    assert "drive-through of From the settings" in instructions


async def test_registering_again_replaces_the_details(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client)

    response = await go_live(
        client,
        restaurant={"name": "Second name", "pickup_minutes": 10},
        items=[item("Lobster roll", "Tacos", 1400)],
    )

    assert response.status_code == 201
    rows = list((await db_session.execute(select(RestaurantProfile))).scalars())
    assert [(row.name, row.address, row.pickup_minutes) for row in rows] == [
        ("Second name", None, 10)
    ]


async def test_seeding_goes_back_to_the_settings(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client)

    await seed_menu(db_session, load_menu(MENU_PATH))

    assert (await db_session.scalar(select(RestaurantProfile.id).limit(1))) is None
    assert "Fish taco" not in await on_menu(client)


@pytest.mark.parametrize(
    "restaurant",
    [
        {"name": "   ", "pickup_minutes": 15},
        {"name": "Two\nlines", "pickup_minutes": 15},
        {"name": "Fine", "pickup_minutes": 0},
        {"name": "Fine", "address": "Two\nlines", "pickup_minutes": 15},
    ],
)
async def test_details_the_agent_could_not_use_are_refused(
    client: httpx.AsyncClient, restaurant: dict[str, Any]
) -> None:
    response = await go_live(client, restaurant=restaurant)

    assert response.status_code == 422


# --- Replacing the menu -----------------------------------------------------


async def test_replacing_takes_off_what_was_there(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await go_live(client, replace_menu=True)

    assert response.json()["items_taken_off"] == 21
    assert response.json()["combos_retired"] == 1
    assert await on_menu(client) == {"Fish taco", "Slaw cup"}
    # Taken off, not deleted: it can be put back from the menu page.
    classic = await db_session.scalar(
        select(MenuItem).where(MenuItem.code == "classic_burger")
    )
    assert classic is not None
    assert classic.is_active is False


async def test_replacing_retires_the_combos_so_old_ones_are_not_offered(
    client: httpx.AsyncClient,
) -> None:
    await go_live(client, replace_menu=True)

    assert (await client.get("/combos")).json() == []


async def test_replacing_lets_an_old_name_be_used_again(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client, replace_menu=True, items=[item("Classic burger", "Burgers", 700)]
    )

    assert response.status_code == 201
    admin = (await client.get("/admin/menu")).json()["items"]
    new = next(i for i in admin if i["name_en"] == "Classic burger" and i["is_active"])
    assert new["item_code"] == "classic_burger_2"
    assert new["base_price_cents"] == 700


async def test_a_replaced_menu_can_be_put_back_by_seeding(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client, replace_menu=True)

    await seed_menu(db_session, load_menu(MENU_PATH))

    names = await on_menu(client)
    assert "Classic burger" in names
    assert "Fish taco" not in names
    assert [c["code"] for c in (await client.get("/combos")).json()] == ["burger_combo"]


# --- All or nothing ---------------------------------------------------------


async def test_a_refusal_leaves_everything_as_it_was(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The last combo is refused after everything else has been written."""
    before = await on_menu(client)

    response = await go_live(
        client,
        replace_menu=True,
        items=[item("Fish taco", "Tacos", 650)],
        combos=[
            {
                "name_en": "Too generous",
                "discount_cents": 5000,
                "slots": ["Tacos", "Tacos"],
            }
        ],
    )

    assert response.status_code == 422
    assert await on_menu(client) == before
    assert (await client.get("/agent")).json()["restaurant_name"] == (
        "Maple Lane Burgers"
    )
    tacos = await db_session.scalar(
        select(MenuCategory.id).where(MenuCategory.code == "tacos")
    )
    assert tacos is None


async def test_a_name_already_on_the_menu_is_refused_when_adding(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(client, items=[item("classic BURGER", "Burgers", 700)])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_item_name"


async def test_the_same_name_twice_in_one_setup_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client, items=[item("Fish taco", "Tacos", 650), item("FISH TACO", "Tacos", 650)]
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_item_name"


async def test_a_setup_needs_an_item(client: httpx.AsyncClient) -> None:
    response = await go_live(client, items=[])

    assert response.status_code == 422


# --- Halal and kosher -------------------------------------------------------


async def test_reviews_reach_what_the_agent_reads(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(
        client,
        items=[
            item(
                "Fish taco",
                "Tacos",
                650,
                diets={
                    "halal": {"status": "suitable", "note": "Fried in its own oil."},
                    "kosher": {"status": "certified", "certified_by": "COR"},
                },
            )
        ],
    )

    halal = await diet_rules.item_diet(db_session, "fish_taco", Diet.HALAL, Language.EN)
    assert halal.status is DietStatus.SUITABLE
    assert halal.note == "Fried in its own oil."
    kosher = await diet_rules.item_diet(
        db_session, "fish_taco", Diet.KOSHER, Language.EN
    )
    assert kosher.status is DietStatus.CERTIFIED
    assert kosher.note == "Certified kosher by COR."


async def test_an_item_left_unreviewed_claims_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client)

    entry = await diet_rules.item_diet(db_session, "fish_taco", Diet.HALAL, Language.EN)

    assert entry.status is None
    assert entry.note is None


async def test_a_review_with_no_reason_gets_a_plain_one_in_both_languages(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(
        client,
        items=[
            item(
                "Fish taco",
                "Tacos",
                650,
                diets={
                    "halal": {"status": "suitable"},
                    "kosher": {"status": "not_suitable"},
                },
            )
        ],
    )

    notes = {
        row.diet: row.note_i18n
        for row in (await db_session.execute(select(DietSuitability))).scalars()
        if "Nothing in it" in row.note_i18n["en"]
        or "not prepared" in row.note_i18n["en"]
    }
    assert notes[Diet.HALAL]["fr"].startswith("Rien d'interdit")
    assert "casher" in notes[Diet.KOSHER]["fr"]


async def test_certified_says_by_whom(client: httpx.AsyncClient) -> None:
    response = await go_live(
        client,
        items=[
            item("Fish taco", "Tacos", 650, diets={"halal": {"status": "certified"}})
        ],
    )

    assert response.status_code == 422


async def test_a_name_cannot_be_given_to_something_not_certified(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client,
        items=[
            item(
                "Fish taco",
                "Tacos",
                650,
                diets={"halal": {"status": "suitable", "certified_by": "COR"}},
            )
        ],
    )

    assert response.status_code == 422


# --- Allergens --------------------------------------------------------------


async def test_a_review_reaches_what_the_agent_reads(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await go_live(
        client,
        items=[
            item(
                "Fish taco",
                "Tacos",
                650,
                allergens={
                    "contains": ["fish", "wheat"],
                    "may_contain": [
                        {"allergen": "sesame", "because": "Shared with the buns."}
                    ],
                },
            )
        ],
    )

    assert response.json()["allergen_reviews"] == 1
    report = await allergen_matrix.item_allergens(
        db_session, "fish_taco", None, None, Language.EN
    )
    assert report.verified
    assert [found.allergen for found in report.contains] == [
        Allergen.FISH,
        Allergen.WHEAT,
    ]
    assert [found.allergen for found in report.may_contain] == [Allergen.SESAME]
    assert report.may_contain[0].because == ["Shared with the buns."]


async def test_a_reason_given_in_english_is_read_out_in_french_too(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(
        client,
        items=[
            item(
                "Fish taco",
                "Tacos",
                650,
                allergens={
                    "may_contain": [
                        {"allergen": "sesame", "because": "Shared with the buns."}
                    ]
                },
            )
        ],
    )

    report = await allergen_matrix.item_allergens(
        db_session, "fish_taco", None, None, Language.FR
    )
    assert report.may_contain[0].because == ["Shared with the buns."]


async def test_an_item_left_blank_stays_unreviewed_and_is_never_offered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A reviewed item with nothing declared is offered to guests avoiding an
    allergen, so a missed tick must not read as a review."""
    response = await go_live(client, items=[item("Fish taco", "Tacos", 650)])

    assert response.json()["allergen_reviews"] == 0
    report = await allergen_matrix.item_allergens(
        db_session, "fish_taco", None, [Allergen.PEANUT], Language.EN
    )
    assert report.verified is False
    offered = await allergen_matrix.items_without(
        db_session, [Allergen.PEANUT], Language.EN, category_code="tacos"
    )
    assert offered == []


async def test_saying_none_of_these_is_a_review_and_can_be_offered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(
        client,
        items=[
            item("Fish taco", "Tacos", 650, allergens={"none_of_these": True}),
        ],
    )

    report = await allergen_matrix.item_allergens(
        db_session, "fish_taco", None, [Allergen.PEANUT], Language.EN
    )
    assert report.verified is True
    offered = await allergen_matrix.items_without(
        db_session, [Allergen.PEANUT], Language.EN, category_code="tacos"
    )
    assert [found.item_code for found in offered] == ["fish_taco"]


@pytest.mark.parametrize(
    "allergens",
    [
        # Saying nothing is not the same as saying none.
        {},
        {"contains": [], "may_contain": []},
        {"none_of_these": False},
        # Both at once: which does it mean?
        {"none_of_these": True, "contains": ["milk"]},
        # May contain needs a reason a guest can hear.
        {"may_contain": [{"allergen": "milk"}]},
        {"may_contain": [{"allergen": "milk", "because": "   "}]},
        # One allergen has one answer.
        {"contains": ["milk", "milk"]},
        {
            "contains": ["milk"],
            "may_contain": [{"allergen": "milk", "because": "Shared fryer."}],
        },
        # Chosen from the twelve, never typed.
        {"contains": ["strawberries"]},
    ],
)
async def test_a_review_that_does_not_say_what_it_means_is_refused(
    client: httpx.AsyncClient, allergens: dict[str, Any]
) -> None:
    response = await go_live(
        client, items=[item("Fish taco", "Tacos", 650, allergens=allergens)]
    )

    assert response.status_code == 422


# --- Sizes ------------------------------------------------------------------

SIZES = [
    {"name_en": "Regular", "price_cents": 650},
    {"name_en": "Large", "price_cents": 900},
]


async def test_the_cheapest_size_is_the_price_and_the_others_add_to_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Typed dearest first, as an owner might.
    response = await go_live(
        client,
        items=[item("Fish taco", "Tacos", 650, sizes=list(reversed(SIZES)))],
    )

    assert response.status_code == 201
    details = await get_item_details(db_session, "fish_taco", Language.EN)
    assert details.base_price_cents == 650
    group = details.choices[0]
    assert (group.group_code, group.min_select, group.max_select) == ("size", 1, 1)
    assert [(o.option_name, o.price_delta_cents) for o in group.options] == [
        ("Large", 250),
        ("Regular", 0),
    ]


async def test_with_no_default_the_agent_has_to_ask(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client, items=[item("Fish taco", "Tacos", 650, sizes=SIZES)])

    details = await get_item_details(db_session, "fish_taco", Language.EN)
    assert details.must_ask == ["size"]
    customer_session = await open_session(db_session, lane="1")
    with pytest.raises(MissingRequiredOptions):
        await add_item(
            db_session, customer_session.id, "fish_taco", 1, tax_rate=TEST_TAX_RATE
        )


async def test_with_a_default_the_agent_assumes_it_at_its_real_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    sizes = [SIZES[0], {**SIZES[1], "is_default": True}]
    await go_live(client, items=[item("Fish taco", "Tacos", 650, sizes=sizes)])

    details = await get_item_details(db_session, "fish_taco", Language.EN)
    assert details.must_ask == []
    assert details.choices[0].default_option_code == "large"
    customer_session = await open_session(db_session, lane="1")
    cart = await add_item(
        db_session, customer_session.id, "fish_taco", 1, tax_rate=TEST_TAX_RATE
    )
    assert cart.lines[0].unit_price_cents == 900


async def test_stock_is_per_item_whatever_the_size(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client, items=[item("Fish taco", "Tacos", 650, sizes=SIZES, stock=5)])

    customer_session = await open_session(db_session, lane="1")
    for size, quantity in (("regular", 2), ("large", 1)):
        await add_item(
            db_session,
            customer_session.id,
            "fish_taco",
            quantity,
            {"size": [size]},
            tax_rate=TEST_TAX_RATE,
        )

    levels = await inventory.stock_levels(db_session)
    assert [level.available for level in levels if level.item_code == "fish_taco"] == [
        2
    ]


async def test_a_combo_counts_an_item_at_its_cheapest_size(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client,
        replace_menu=True,
        items=[
            item("Fish taco", "Tacos", 650, sizes=SIZES),
            item("Slaw cup", "Extras", 300),
        ],
        combos=[{**TACO_PLATE, "discount_cents": 950}],
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["cheapest_set_cents"] == 950


@pytest.mark.parametrize(
    ("price", "sizes"),
    [
        # The item's price and the sizes disagree.
        (700, SIZES),
        # One size is no choice.
        (650, SIZES[:1]),
        # Two of the same, however they are written.
        (650, [SIZES[0], {"name_en": "REGULAR!", "price_cents": 700}]),
        # Only one can be assumed.
        (650, [{**size, "is_default": True} for size in SIZES]),
        (650, [SIZES[0], {"name_en": "  ", "price_cents": 700}]),
        (650, [SIZES[0], {"name_en": "Large", "price_cents": -1}]),
    ],
)
async def test_sizes_that_do_not_add_up_are_refused(
    client: httpx.AsyncClient, price: int, sizes: list[dict[str, Any]]
) -> None:
    response = await go_live(
        client, items=[item("Fish taco", "Tacos", price, sizes=sizes)]
    )

    assert response.status_code == 422


# --- Combos -----------------------------------------------------------------

TACO_PLATE = {
    "name_en": "Taco plate",
    "discount_cents": 150,
    "slots": ["Tacos", "Extras"],
}


async def test_a_combo_is_offered_and_taken_off_like_any_other(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(client, replace_menu=True, combos=[TACO_PLATE])

    combos = (await client.get("/combos")).json()
    assert [c["code"] for c in combos] == ["taco_plate"]
    assert [slot["category_code"] for slot in combos[0]["slots"]] == ["tacos", "extras"]

    customer_session = await open_session(db_session, lane="1")
    await add_item(
        db_session, customer_session.id, "fish_taco", 1, tax_rate=TEST_TAX_RATE
    )
    await add_item(
        db_session, customer_session.id, "slaw_cup", 1, tax_rate=TEST_TAX_RATE
    )
    cart = await get_cart(db_session, customer_session.id, tax_rate=TEST_TAX_RATE)

    assert [discount.code for discount in cart.discounts] == ["taco_plate"]
    assert cart.discounts[0].amount_cents == 150


async def test_a_new_combo_comes_after_the_ones_already_there(
    client: httpx.AsyncClient,
) -> None:
    await go_live(client, combos=[TACO_PLATE])

    codes = [c["code"] for c in (await client.get("/combos")).json()]
    assert codes == ["burger_combo", "taco_plate"]


async def test_a_combo_can_use_a_section_already_on_the_menu(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client,
        combos=[
            {"name_en": "Dinner", "discount_cents": 100, "slots": ["Tacos", "Drinks"]}
        ],
    )

    assert response.status_code == 201


async def test_a_combo_needs_two_parts(client: httpx.AsyncClient) -> None:
    response = await go_live(
        client, combos=[{"name_en": "One", "discount_cents": 100, "slots": ["Tacos"]}]
    )

    assert response.status_code == 422


async def test_a_combo_needs_items_in_each_part(client: httpx.AsyncClient) -> None:
    response = await go_live(
        client,
        combos=[
            {"name_en": "Dinner", "discount_cents": 100, "slots": ["Tacos", "Wine"]}
        ],
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "combo_needs_items"
    assert error["details"]["section"] == "Wine"


async def test_a_section_whose_items_all_came_off_has_none(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client,
        replace_menu=True,
        combos=[
            {"name_en": "Old", "discount_cents": 100, "slots": ["Tacos", "Drinks"]}
        ],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "combo_needs_items"


async def test_a_saving_as_large_as_the_cheapest_set_is_refused(
    client: httpx.AsyncClient,
) -> None:
    # Fish taco 650 + Slaw cup 300.
    response = await go_live(
        client,
        replace_menu=True,
        combos=[{**TACO_PLATE, "discount_cents": 950}],
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "combo_saving_too_large"
    assert error["details"]["cheapest_set_cents"] == 950


async def test_a_saving_just_under_the_cheapest_set_is_allowed(
    client: httpx.AsyncClient,
) -> None:
    response = await go_live(
        client,
        replace_menu=True,
        combos=[{**TACO_PLATE, "discount_cents": 949}],
    )

    assert response.status_code == 201


async def test_a_combo_name_in_use_is_refused(client: httpx.AsyncClient) -> None:
    response = await go_live(client, combos=[{**TACO_PLATE, "name_en": "BURGER COMBO"}])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_combo_name"


async def test_a_retired_combo_name_can_be_used_again_with_a_new_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    response = await go_live(
        client,
        replace_menu=True,
        combos=[{**TACO_PLATE, "name_en": "Burger combo"}],
    )

    assert response.status_code == 201
    active = list(
        (await db_session.execute(select(ComboRule.code).where(ComboRule.is_active)))
        .scalars()
        .all()
    )
    assert active == ["burger_combo_2"]


# --- Stock ------------------------------------------------------------------


async def test_a_counted_item_sells_down_and_an_unlimited_one_does_not(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await go_live(
        client,
        items=[
            item("Fish taco", "Tacos", 650),
            item("Slaw cup", "Extras", 300, stock=2),
        ],
    )

    levels = {level.item_code for level in await inventory.stock_levels(db_session)}

    assert "slaw_cup" in levels
    assert "fish_taco" not in levels
