"""Plays a scenario's customer lines to the agent and collects what happened."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.loop import AgentConfig, run_turn
from drivetruagent.agent.model import ModelClient
from drivetruagent.db.models import Order
from drivetruagent.enums import FINAL_SESSION_STATES, Channel
from drivetruagent.evals.scenario import (
    Exchange,
    ExpectedLine,
    Scenario,
    ScenarioResult,
)
from drivetruagent.ordering.cart import add_item, get_cart, request_confirmation
from drivetruagent.ordering.orders import get_order, submit_order
from drivetruagent.ordering.sessions import open_session


async def run_scenario(
    db: AsyncSession,
    scenario: Scenario,
    *,
    model: ModelClient,
    config: AgentConfig,
    lane: str = "eval",
) -> ScenarioResult:
    """Open a session, say each customer line in turn, and return the outcome.

    Stops early if the session ends (order placed, or handed to a person):
    there is nobody left to talk to.
    """
    await _place_past_orders(db, scenario, config)
    customer_session = (
        await open_session(
            db,
            business_date=scenario.business_date,
            channel=Channel.PHONE,
            caller_phone=scenario.caller_phone,
        )
        if scenario.caller_phone is not None
        else await open_session(db, lane, business_date=scenario.business_date)
    )
    await db.commit()
    session_id = customer_session.id

    exchanges: list[Exchange] = []
    for line in scenario.customer:
        reply = await run_turn(db, session_id, line, model=model, config=config)
        exchanges.append(Exchange(line, reply.text, reply.tools_used))
        if reply.cart.state in FINAL_SESSION_STATES:
            break

    cart = await get_cart(db, session_id, tax_rate=config.tax_rate)
    order_id = (
        await db.execute(select(Order.id).where(Order.session_id == session_id))
    ).scalar_one_or_none()
    order = await get_order(db, order_id) if order_id else None
    return ScenarioResult(tuple(exchanges), cart, order)


async def _place_past_orders(
    db: AsyncSession, scenario: Scenario, config: AgentConfig
) -> None:
    count = len(scenario.past_orders)
    for days_before, lines in zip(
        range(count, 0, -1), scenario.past_orders, strict=True
    ):
        session = await open_session(
            db,
            business_date=scenario.business_date - timedelta(days=days_before),
            channel=Channel.PHONE,
            caller_phone=scenario.caller_phone,
        )
        for line in lines:
            await add_item(
                db,
                session.id,
                line.item_code,
                line.quantity,
                _selections(line),
                tax_rate=config.tax_rate,
            )
        await request_confirmation(db, session.id, tax_rate=config.tax_rate)
        await submit_order(db, session.id, tax_rate=config.tax_rate)
    await db.commit()


def _selections(line: ExpectedLine) -> dict[str, list[str]]:
    return {group: [option] for group, option in line.options.items()}
