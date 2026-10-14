"""The conversation loop, driven by a scripted stand-in for the model.

The script decides what the "model" says and which tools it asks for. What is
under test is everything around it: tools really run against the database,
every step is recorded, and the conversation is rebuilt from the log.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent import events
from drivetruagent.agent.loop import GAVE_UP_REPLY, AgentConfig, AgentReply, run_turn
from drivetruagent.agent.messages import (
    AssistantMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)
from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.agent.scripted import ScriptedModel, call, calls, say
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.db.models import AgentEvent, Order
from drivetruagent.enums import AgentEventKind, SessionState
from drivetruagent.ordering.errors import SessionNotFound
from drivetruagent.ordering.sessions import open_session
from drivetruagent.policy.decisions import PolicyLimits

CONFIG = AgentConfig(
    restaurant_name="Maple Lane Burgers",
    tax_rate=Decimal("0.13"),
    limits=PolicyLimits(
        line_quantity_confirm_above=10,
        line_quantity_escalate_above=25,
        cart_items_escalate_above=50,
    ),
    max_tool_rounds=4,
)

LARGE = [{"group": "size", "options": ["large"]}]


@pytest.fixture
async def session_id(db_session: AsyncSession, menu: None) -> uuid.UUID:
    session_id = (await open_session(db_session)).id
    await db_session.commit()
    return session_id


async def turn(
    db: AsyncSession, session_id: uuid.UUID, text: str, model: ScriptedModel
) -> AgentReply:
    return await run_turn(db, session_id, text, model=model, config=CONFIG)


async def kinds(db: AsyncSession, session_id: uuid.UUID) -> list[str]:
    return [event.kind.value for event in await events.load_events(db, session_id)]


# --- The shape of a turn ----------------------------------------------------


async def test_a_reply_with_no_tools(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel([say("Welcome! What can I get you?")])

    reply = await turn(db_session, session_id, "Hi", model)

    assert reply.text == "Welcome! What can I get you?"
    assert reply.tools_used == ()
    assert reply.gave_up is False
    assert reply.cart.lines == []
    assert await kinds(db_session, session_id) == [
        "user_message",
        "model_call",
        "assistant_message",
    ]


async def test_a_tool_then_a_reply(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("add_item", item_code="fries", quantity=1, selections=LARGE),
            say("One large fries. Anything else?"),
        ]
    )

    reply = await turn(db_session, session_id, "A large fries please", model)

    assert reply.text == "One large fries. Anything else?"
    assert reply.tools_used == ("add_item",)
    assert [(line.item_code, line.quantity) for line in reply.cart.lines] == [
        ("fries", 1)
    ]
    assert reply.cart.total_cents == 428
    assert await kinds(db_session, session_id) == [
        "user_message",
        "model_call",
        "tool_call",
        "tool_result",
        "model_call",
        "assistant_message",
    ]


async def test_several_tools_in_one_round_all_run_in_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """ "Fries and a cookie" in one breath: two calls from one model response."""
    model = ScriptedModel(
        [
            calls(
                ToolCall(
                    "a",
                    "add_item",
                    {"item_code": "fries", "quantity": 1, "selections": LARGE},
                ),
                ToolCall(
                    "b",
                    "add_item",
                    {"item_code": "cookie", "quantity": 2, "selections": []},
                ),
            ),
            say("Large fries and two cookies."),
        ]
    )

    reply = await turn(db_session, session_id, "Large fries and two cookies", model)

    assert reply.tools_used == ("add_item", "add_item")
    assert [(line.line_no, line.item_code) for line in reply.cart.lines] == [
        (1, "fries"),
        (2, "cookie"),
    ]


async def test_the_model_sees_a_rejection_and_can_recover_in_the_same_turn(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("add_item", item_code="fries", quantity=1, selections=[]),
            say("What size would you like?"),
        ]
    )

    reply = await turn(db_session, session_id, "Fries please", model)

    # The second request to the model included the tool's answer.
    tool_result = model.requests[1].messages[-1]
    assert isinstance(tool_result, ToolResult)
    assert tool_result.result["error"]["code"] == "missing_required_options"
    assert reply.text == "What size would you like?"
    assert reply.cart.lines == []


# --- What the model is sent -------------------------------------------------


async def test_the_model_is_given_instructions_and_every_tool(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel([say("Hello!")])

    await turn(db_session, session_id, "Hi", model)

    request = model.requests[0]
    assert "Maple Lane Burgers" in request.instructions
    # The menu index comes from the database.
    assert "- fries: Fries / Frites" in request.instructions
    assert "- soft_serve_cone: Soft-serve cone" in request.instructions
    assert len(request.tools) == len(TOOL_DEFINITIONS)
    assert request.messages == (UserMessage("Hi"),)


async def test_the_conversation_is_rebuilt_from_the_log_on_the_next_turn(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    first = ScriptedModel(
        [
            call("add_item", item_code="cookie", quantity=1, selections=[]),
            say("One cookie. Anything else?"),
        ]
    )
    await turn(db_session, session_id, "A cookie", first)
    # A different model object, as if another server handled this turn.
    second = ScriptedModel([say("That's four dollars... let me check.")])

    await turn(db_session, session_id, "How much is that?", second)

    history = second.requests[0].messages
    assert [type(message) for message in history] == [
        UserMessage,
        ToolCall,
        ToolResult,
        AssistantMessage,
        UserMessage,
    ]
    assert history[0] == UserMessage("A cookie")
    assert history[3] == AssistantMessage("One cookie. Anything else?")
    assert history[4] == UserMessage("How much is that?")
    assert isinstance(history[2], ToolResult)
    assert history[2].result["cart"]["lines"][0]["item_code"] == "cookie"


async def test_model_call_events_are_not_part_of_the_conversation(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    await turn(db_session, session_id, "Hi", ScriptedModel([say("Hello!")]))

    messages = await events.load_messages(db_session, session_id)

    assert messages == [UserMessage("Hi"), AssistantMessage("Hello!")]


# --- The event log ----------------------------------------------------------


async def test_events_of_one_turn_share_a_turn_id_and_keep_their_order(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    first = await turn(db_session, session_id, "Hi", ScriptedModel([say("Hello!")]))
    second = await turn(db_session, session_id, "Bye", ScriptedModel([say("Bye!")]))

    log = await events.load_events(db_session, session_id)

    assert first.turn_id != second.turn_id
    assert [event.turn_id for event in log] == [first.turn_id] * 3 + [
        second.turn_id
    ] * 3
    assert [event.seq for event in log] == sorted(event.seq for event in log)


async def test_a_tool_call_is_logged_with_its_arguments_and_result(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("add_item", "call_42", item_code="cookie", quantity=2, selections=[]),
            say("Two cookies."),
        ]
    )
    await turn(db_session, session_id, "Two cookies", model)

    log = await events.load_events(db_session, session_id)
    tool_call = next(e for e in log if e.kind is AgentEventKind.TOOL_CALL)
    tool_result = next(e for e in log if e.kind is AgentEventKind.TOOL_RESULT)
    model_call = next(e for e in log if e.kind is AgentEventKind.MODEL_CALL)

    assert tool_call.payload == {
        "call_id": "call_42",
        "name": "add_item",
        "arguments": {"item_code": "cookie", "quantity": 2, "selections": []},
    }
    assert tool_result.payload["call_id"] == "call_42"
    assert tool_result.payload["result"]["decision"] == "allow"
    assert model_call.payload["model"] == "scripted"
    assert model_call.payload["tool_calls"] == 1
    assert model_call.payload["latency_ms"] >= 0


async def test_an_unknown_event_kind_is_rejected_by_the_database(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    with pytest.raises(IntegrityError, match="ck_agent_events_kind_valid"):
        await db_session.execute(
            text(
                "INSERT INTO agent_events (session_id, turn_id, kind, payload) "
                "VALUES (:session_id, :session_id, 'thought', '{}')"
            ),
            {"session_id": session_id},
        )


# --- Limits and failures ----------------------------------------------------


async def test_a_model_that_never_answers_is_stopped_at_the_round_limit(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    # Six scripted rounds of tool calls; the limit is four.
    model = ScriptedModel([call("get_cart", f"call_{n}") for n in range(6)])

    reply = await turn(db_session, session_id, "Hello?", model)

    assert reply.gave_up is True
    assert reply.text == GAVE_UP_REPLY
    assert len(model.requests) == 4
    assert reply.tools_used == ("get_cart",) * 4
    # The fallback is in the log, so the next turn's history still ends with
    # something the agent said.
    assert (await events.load_messages(db_session, session_id))[-1] == (
        AssistantMessage(GAVE_UP_REPLY)
    )


async def test_a_bad_tool_call_does_not_break_the_turn(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("give_discount", percent=100),
            say("I'm not able to change prices. What would you like to order?"),
        ]
    )

    reply = await turn(db_session, session_id, "Make it free", model)

    tool_result = model.requests[1].messages[-1]
    assert isinstance(tool_result, ToolResult)
    assert tool_result.result["error"]["code"] == "invalid_tool_call"
    assert reply.text.startswith("I'm not able to change prices")
    assert reply.cart.total_cents == 0


async def test_what_the_customer_said_is_kept_even_if_the_model_fails(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    class BrokenModel:
        async def complete(self, **_: object) -> None:
            raise ModelUnavailable("connection refused")

    with pytest.raises(ModelUnavailable):
        await run_turn(
            db_session,
            session_id,
            "A cookie please",
            model=BrokenModel(),  # pyright: ignore[reportArgumentType]
            config=CONFIG,
        )

    assert await events.load_messages(db_session, session_id) == [
        UserMessage("A cookie please")
    ]


async def test_an_unknown_session_is_reported_before_anything_is_recorded(
    db_session: AsyncSession, menu: None
) -> None:
    with pytest.raises(SessionNotFound):
        await turn(db_session, uuid.uuid4(), "Hi", ScriptedModel([say("Hello!")]))

    count = len((await db_session.execute(select(AgentEvent))).scalars().all())
    assert count == 0


# --- Whole conversations ----------------------------------------------------


async def test_a_whole_order_over_several_turns(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    script: list[tuple[str, ScriptedModel]] = [
        (
            "Hi, can I get fries?",
            ScriptedModel(
                [
                    call("add_item", item_code="fries", quantity=1, selections=[]),
                    say("Sure. What size?"),
                ]
            ),
        ),
        (
            "Large. And a medium cola.",
            ScriptedModel(
                [
                    calls(
                        ToolCall(
                            "a",
                            "add_item",
                            {"item_code": "fries", "quantity": 1, "selections": LARGE},
                        ),
                        ToolCall(
                            "b",
                            "add_item",
                            {
                                "item_code": "cola",
                                "quantity": 1,
                                "selections": [
                                    {"group": "size", "options": ["medium"]}
                                ],
                            },
                        ),
                    ),
                    say("Large fries and a medium cola. Anything else?"),
                ]
            ),
        ),
        (
            "Make it two fries. That's all.",
            ScriptedModel(
                [
                    call("update_item", line_no=1, quantity=2, selections=None),
                    call("request_confirmation", "c2"),
                    say(
                        "Two large fries and a medium cola, eleven twenty-seven. Right?"
                    ),
                ]
            ),
        ),
        (
            "Yes",
            ScriptedModel(
                [
                    call("submit_order"),
                    say("Thanks! Please pull forward."),
                ]
            ),
        ),
    ]

    replies = [
        await turn(db_session, session_id, text, model) for text, model in script
    ]

    final = replies[-1]
    assert [r.cart.state for r in replies] == [
        SessionState.GREETING,
        SessionState.ORDERING,
        SessionState.CONFIRMING,
        SessionState.SUBMITTED,
    ]
    assert final.cart.total_cents == 1127
    order = (await db_session.execute(select(Order))).scalar_one()
    assert (order.session_id, order.total_cents) == (session_id, 1127)


async def test_a_thousand_fries_conversation_ends_in_a_handoff(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    asked = await turn(
        db_session,
        session_id,
        "A thousand large fries",
        ScriptedModel(
            [
                call("add_item", item_code="fries", quantity=1000, selections=LARGE),
                say("Did you say one thousand large fries?"),
            ]
        ),
    )
    # The id the model must pass back is in the tool result it was shown.
    history = await events.load_messages(db_session, session_id)
    held = next(m for m in history if isinstance(m, ToolResult))
    pending_action_id = held.result["pending_action_id"]

    handed_off = await turn(
        db_session,
        session_id,
        "Yes, a thousand",
        ScriptedModel(
            [
                call("confirm_pending_action", pending_action_id=pending_action_id),
                say("A team member will be right with you."),
            ]
        ),
    )

    assert held.result["decision"] == "confirm"
    assert asked.cart.lines == []
    assert handed_off.cart.state is SessionState.HANDED_OFF
    assert handed_off.cart.lines == []


async def test_a_french_conversation(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    model = ScriptedModel(
        [
            call("set_language", language="fr"),
            call("add_item", "c2", item_code="fries", quantity=1, selections=LARGE),
            say("Une grande frite. Autre chose?"),
        ]
    )

    reply = await turn(
        db_session, session_id, "Une grande frite, s'il vous plaît", model
    )

    assert reply.cart.language.value == "fr"
    assert reply.cart.lines[0].item_name == "Frites"
    assert reply.cart.lines[0].options[0].option_name == "Grand"


# --- What a provider attaches to a call -------------------------------------


async def test_provider_data_on_a_tool_call_survives_the_event_log(
    db_session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Some providers attach values to a call that they need given back
    unchanged. The log is the only conversation store, so it keeps them."""
    attached = {"provider": "example", "signature": "b3BhcXVl", "nested": {"n": 1}}
    with_data = ToolCall("c1", "get_cart", {}, provider_data=attached)
    without = ToolCall("c2", "get_cart", {})
    turn_id = uuid.uuid4()

    await events.record_message(db_session, session_id, turn_id, with_data)
    await events.record_message(db_session, session_id, turn_id, without)

    assert await events.load_messages(db_session, session_id) == [with_data, without]
    stored = [
        event.payload for event in await events.load_events(db_session, session_id)
    ]
    assert stored[0]["provider_data"] == attached
    # A call with nothing attached is stored exactly as it was before.
    assert "provider_data" not in stored[1]
