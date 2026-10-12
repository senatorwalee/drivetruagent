"""The agent's endpoints: propose an action, then answer a held one."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.factories import make_session

pytestmark = pytest.mark.usefixtures("menu")

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
async def session_id(client: httpx.AsyncClient, menu: None) -> str:
    return (await client.post("/sessions")).json()["session_id"]


async def propose(
    client: httpx.AsyncClient, session_id: str, action_type: str, **fields: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/sessions/{session_id}/actions", json={"type": action_type, **fields}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def answer(
    client: httpx.AsyncClient, session_id: str, outcome: dict[str, Any], reply: str
) -> dict[str, Any]:
    response = await client.post(
        f"/sessions/{session_id}/pending-actions/{outcome['pending_action_id']}/{reply}"
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_whole_order_through_the_agent_endpoints(
    client: httpx.AsyncClient, session_id: str
) -> None:
    # "Fries, please." No size yet: rejected, with the sizes to offer.
    no_size = await propose(client, session_id, "add_item", item_code="fries")
    assert (no_size["decision"], no_size["rule"]) == (
        "reject",
        "missing_required_options",
    )
    assert no_size["error"]["details"]["missing"][0]["group_code"] == "size"

    # "Large."
    added = await propose(
        client,
        session_id,
        "add_item",
        item_code="fries",
        selections={"size": ["large"]},
    )
    assert added["decision"] == "allow"
    assert added["cart"]["total_cents"] == 428  # 379 + 13% = 428.27

    # Trying to place the order before reading it back is refused.
    too_soon = await propose(client, session_id, "submit_order")
    assert (too_soon["decision"], too_soon["rule"]) == ("reject", "order_not_confirmed")

    # Read back, then place it.
    read_back = await propose(client, session_id, "request_confirmation")
    assert read_back["cart"]["state"] == "confirming"
    placed = await propose(client, session_id, "submit_order")
    assert placed["decision"] == "allow"
    assert placed["order"]["total_cents"] == 428
    assert placed["cart"]["state"] == "submitted"

    # The order really exists.
    stored = await client.get(f"/orders/{placed['order']['order_id']}")
    assert stored.json() == placed["order"]


async def test_unusual_quantity_is_held_until_the_customer_says_yes(
    client: httpx.AsyncClient, session_id: str
) -> None:
    held = await propose(
        client, session_id, "add_item", item_code="cookie", quantity=15
    )
    cart_while_waiting = (await client.get(f"/sessions/{session_id}/cart")).json()

    confirmed = await answer(client, session_id, held, "confirm")

    assert (held["decision"], held["rule"]) == ("confirm", "unusual_quantity")
    assert held["pending_action_id"] is not None
    assert cart_while_waiting["lines"] == []
    assert confirmed["decision"] == "allow"
    assert confirmed["cart"]["item_count"] == 15


async def test_the_customer_can_say_no(
    client: httpx.AsyncClient, session_id: str
) -> None:
    held = await propose(
        client, session_id, "add_item", item_code="cookie", quantity=15
    )

    cancelled = await answer(client, session_id, held, "cancel")

    assert (cancelled["decision"], cancelled["rule"]) == (
        "allow",
        "pending_action_cancelled",
    )
    assert cancelled["cart"]["lines"] == []


async def test_a_thousand_fries_ends_in_a_handoff(
    client: httpx.AsyncClient, session_id: str
) -> None:
    held = await propose(
        client,
        session_id,
        "add_item",
        item_code="fries",
        quantity=1000,
        selections={"size": ["large"]},
    )

    outcome = await answer(client, session_id, held, "confirm")

    session = (await client.get(f"/sessions/{session_id}")).json()
    assert held["decision"] == "confirm"
    assert (outcome["decision"], outcome["rule"]) == ("escalate", "extreme_quantity")
    assert (session["state"], session["end_reason"]) == (
        "handed_off",
        "extreme_quantity",
    )
    assert outcome["cart"]["lines"] == []


async def test_a_rejection_is_still_a_200(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A refusal is a normal answer to a proposal, not a failed request."""
    response = await client.post(
        f"/sessions/{session_id}/actions",
        json={"type": "add_item", "item_code": "lobster"},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "reject"
    assert response.json()["error"]["code"] == "unknown_item"


@pytest.mark.parametrize(
    "body",
    [
        {"type": "give_discount", "percent": 100},
        {"type": "add_item", "item_code": "fries", "unit_price_cents": 1},
        {"type": "submit_order", "total_cents": 0},
        {"item_code": "fries"},
        {},
    ],
)
async def test_an_action_that_is_not_one_of_the_allowed_shapes_is_refused(
    client: httpx.AsyncClient, session_id: str, body: dict[str, Any]
) -> None:
    """There is no way to phrase a price change as an action."""
    response = await client.post(f"/sessions/{session_id}/actions", json=body)

    assert response.status_code == 422
    assert (await client.get(f"/sessions/{session_id}/cart")).json()["lines"] == []


@pytest.mark.parametrize(
    "path",
    [
        f"/sessions/{UNKNOWN_ID}/actions",
        f"/sessions/{UNKNOWN_ID}/pending-actions/{UNKNOWN_ID}/confirm",
        f"/sessions/{UNKNOWN_ID}/pending-actions/{UNKNOWN_ID}/cancel",
    ],
)
async def test_unknown_session_is_not_found(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.post(path, json={"type": "submit_order"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize("reply", ["confirm", "cancel"])
async def test_unknown_pending_action_is_not_found(
    client: httpx.AsyncClient, session_id: str, reply: str
) -> None:
    response = await client.post(
        f"/sessions/{session_id}/pending-actions/{UNKNOWN_ID}/{reply}"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "pending_action_not_found"


async def test_the_decision_is_committed(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A held action must still be there when the answer arrives later."""
    held = await propose(
        client, session_id, "add_item", item_code="cookie", quantity=15
    )
    await client.get(f"/sessions/{session_id}/cart")  # an unrelated request

    confirmed = await answer(client, session_id, held, "confirm")

    assert confirmed["decision"] == "allow"


# --- The pending_actions table ----------------------------------------------


async def insert_pending(db: AsyncSession, session_id: object, **columns: str) -> None:
    values = {
        "status": "'pending'",
        "resolved_at": "NULL",
        "cart_version": "0",
        **columns,
    }
    await db.execute(
        text(
            "INSERT INTO pending_actions "
            "(session_id, action, rule, message, status, resolved_at, cart_version) "
            f"VALUES (:session_id, '{{}}', 'unusual_quantity', 'ask', "
            f"{values['status']}, {values['resolved_at']}, {values['cart_version']})"
        ),
        {"session_id": session_id},
    )


async def test_only_one_pending_action_per_session(db_session: AsyncSession) -> None:
    customer_session = await make_session(db_session)
    await insert_pending(db_session, customer_session.id)

    with pytest.raises(
        IntegrityError, match="uq_pending_actions_one_pending_per_session"
    ):
        await insert_pending(db_session, customer_session.id)


async def test_resolved_actions_do_not_block_a_new_pending_one(
    db_session: AsyncSession,
) -> None:
    customer_session = await make_session(db_session)
    await insert_pending(
        db_session, customer_session.id, status="'cancelled'", resolved_at="now()"
    )

    await insert_pending(db_session, customer_session.id)


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        (
            {"status": "'confirmed'"},
            "ck_pending_actions_resolved_at_matches_status",
        ),
        ({"resolved_at": "now()"}, "ck_pending_actions_resolved_at_matches_status"),
        (
            {"status": "'maybe'", "resolved_at": "now()"},
            "ck_pending_actions_status_valid",
        ),
        ({"cart_version": "-1"}, "ck_pending_actions_cart_version_non_negative"),
    ],
)
async def test_invalid_pending_action_rows_are_rejected(
    db_session: AsyncSession, columns: dict[str, str], constraint: str
) -> None:
    customer_session = await make_session(db_session)

    with pytest.raises(IntegrityError, match=constraint):
        await insert_pending(db_session, customer_session.id, **columns)
