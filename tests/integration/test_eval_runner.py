"""The eval runner, driven by a scripted stand-in for the model.

This checks the plumbing: a scenario is played, the outcome is collected, and
a wrong outcome is reported. The real evals, with the real model, are in
tests/integration/evals and run with `make eval`.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.model import ModelTurn
from drivetruagent.agent.scripted import ScriptedModel, call, say
from drivetruagent.enums import SessionState
from drivetruagent.evals.runner import run_scenario
from drivetruagent.evals.scenario import ExpectedLine, Scenario, check
from drivetruagent.policy.decisions import PolicyLimits

pytestmark = pytest.mark.usefixtures("menu")

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

COOKIE_ORDER = Scenario(
    name="cookie",
    customer=("A cookie please.", "That's all.", "Yes."),
    final_state=SessionState.SUBMITTED,
    lines=(ExpectedLine("cookie", 1),),
    total_cents=168,
    must_use=frozenset({"add_item", "request_confirmation", "submit_order"}),
)


def a_good_agent() -> list[ModelTurn]:
    return [
        call("add_item", item_code="cookie", quantity=1, selections=[]),
        say("One cookie. Anything else?"),
        call("request_confirmation"),
        say("One cookie, one sixty-eight. Is that right?"),
        call("submit_order"),
        say("Thanks! Please pull forward."),
    ]


async def test_a_scenario_played_correctly_passes(db_session: AsyncSession) -> None:
    result = await run_scenario(
        db_session, COOKIE_ORDER, model=ScriptedModel(a_good_agent()), config=CONFIG
    )

    assert check(COOKIE_ORDER, result) == []
    assert result.order is not None
    assert result.order.total_cents == 168
    assert [exchange.customer for exchange in result.exchanges] == list(
        COOKIE_ORDER.customer
    )
    assert result.exchanges[0].tools_used == ("add_item",)


async def test_an_agent_that_adds_the_wrong_thing_fails(
    db_session: AsyncSession,
) -> None:
    turns = a_good_agent()
    turns[0] = call("add_item", item_code="apple_pie", quantity=2, selections=[])

    result = await run_scenario(
        db_session, COOKIE_ORDER, model=ScriptedModel(turns), config=CONFIG
    )

    failures = check(COOKIE_ORDER, result)
    assert "cart is missing 1 x cookie" in failures
    assert "cart has an unexpected line: 2 x apple_pie" in failures
    assert any(failure.startswith("total is") for failure in failures)


async def test_an_agent_that_never_submits_fails(db_session: AsyncSession) -> None:
    turns = [
        call("add_item", item_code="cookie", quantity=1, selections=[]),
        say("One cookie. Anything else?"),
        say("Okay!"),
        say("Great!"),
    ]

    result = await run_scenario(
        db_session, COOKIE_ORDER, model=ScriptedModel(turns), config=CONFIG
    )

    failures = check(COOKIE_ORDER, result)
    assert "session ended as ordering, expected submitted" in failures
    assert "tool submit_order was never used" in failures
    assert result.order is None


async def test_the_runner_stops_when_the_session_ends(
    db_session: AsyncSession,
) -> None:
    """After a handoff there is nobody left to talk to."""
    scenario = Scenario(
        name="asks_for_a_person",
        customer=("Can I talk to a person?", "Hello?", "Anyone there?"),
        final_state=SessionState.HANDED_OFF,
    )
    model = ScriptedModel(
        [
            call("request_human", reason="customer asked"),
            say("A team member will be right with you."),
        ]
    )

    result = await run_scenario(db_session, scenario, model=model, config=CONFIG)

    assert check(scenario, result) == []
    assert len(result.exchanges) == 1


# --- The day a scenario is played on ----------------------------------------


def a_chicken_burger_order() -> list[ModelTurn]:
    spice = [{"group": "spice", "options": ["mild"]}]
    return [
        call("get_todays_specials"),
        say("Today's special is the crispy chicken burger."),
        call("add_item", item_code="chicken_burger", quantity=1, selections=spice),
        say("One chicken burger. Anything else?"),
        call("request_confirmation"),
        say("One chicken burger. Is that right?"),
        call("submit_order"),
        say("Thanks! Please pull forward."),
    ]


CHICKEN_BURGER_ORDER = Scenario(
    name="chicken_burger",
    customer=("What's the special?", "One of those, mild.", "That's all.", "Yes."),
    final_state=SessionState.SUBMITTED,
    lines=(ExpectedLine("chicken_burger", 1, {"spice": "mild"}),),
    must_use=frozenset({"get_todays_specials"}),
)


@pytest.mark.usefixtures("specials")
async def test_a_scenario_is_played_on_its_own_day_not_todays(
    db_session: AsyncSession,
) -> None:
    """The same order on a Monday and on a Friday: only Monday has the chicken
    burger on special. Neither depends on when the test is run."""
    monday = replace(CHICKEN_BURGER_ORDER, discount_cents=150, total_cents=564)
    friday = replace(
        CHICKEN_BURGER_ORDER,
        business_date=date(2026, 10, 16),
        discount_cents=0,
        total_cents=733,
    )

    on_monday = await run_scenario(
        db_session,
        monday,
        model=ScriptedModel(a_chicken_burger_order()),
        config=CONFIG,
        lane="eval-monday",
    )
    on_friday = await run_scenario(
        db_session,
        friday,
        model=ScriptedModel(a_chicken_burger_order()),
        config=CONFIG,
        lane="eval-friday",
    )

    assert check(monday, on_monday) == []
    assert check(friday, on_friday) == []
    assert check(friday, on_monday) == [
        "total is 564 cents, expected 733",
        "discount is 150 cents, expected 0",
    ]
