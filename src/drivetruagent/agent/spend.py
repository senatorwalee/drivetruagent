"""What the model has cost: for one session as it runs, and for a day.

Every model call is already in the event log with its tokens; the notes
written after each session keep theirs on the summary row. This reads both
and prices them with agent/costs.py. Nothing is stored: change the price
list and every figure follows.
"""

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.costs import Cost, PriceList, call_cost
from drivetruagent.agent.summarizer import summary_call
from drivetruagent.db.models import AgentEvent, CustomerSession, Order, SessionSummary
from drivetruagent.enums import AgentEventKind, Channel
from drivetruagent.ordering.errors import SessionNotFound

# What a model call was for. Calls logged before this was recorded were
# the agent's own turns.
TURN = "turn"
GUARDRAIL = "guardrail"
SUMMARY = "summary"


class SessionSpend(BaseModel):
    session_id: uuid.UUID
    # The conversation's model calls: the agent's turns and the guardrail.
    conversation: Cost
    # Writing the note afterwards, once it has been written.
    summary: Cost


class DaySpend(BaseModel):
    business_date: date
    # Sessions in which the customer said something.
    conversations: int
    orders: int
    total: Cost
    # Split by what the calls were for: turn, guardrail, summary.
    by_purpose: dict[str, Cost]
    # Split by how customers reached the agent. Notes are not included.
    by_channel: dict[Channel, Cost]
    # None until there is something to divide by.
    per_conversation_micro_usd: int | None
    per_order_micro_usd: int | None
    # Where the prices came from, and when they were checked.
    prices_checked_on: date
    prices_source: str


async def session_spend(
    db: AsyncSession, session_id: uuid.UUID, prices: PriceList
) -> SessionSpend:
    """Raises SessionNotFound."""
    if await db.get(CustomerSession, session_id) is None:
        raise SessionNotFound(session_id)
    payloads = (
        await db.execute(
            select(AgentEvent.payload).where(
                AgentEvent.session_id == session_id,
                AgentEvent.kind == AgentEventKind.MODEL_CALL,
            )
        )
    ).scalars()
    conversation = Cost()
    for payload in payloads:
        conversation += call_cost(payload, prices)
    summary = (
        await db.execute(
            select(SessionSummary).where(SessionSummary.session_id == session_id)
        )
    ).scalar_one_or_none()
    return SessionSpend(
        session_id=session_id,
        conversation=conversation,
        summary=_summary_cost(summary, prices),
    )


async def day_spend(
    db: AsyncSession, business_date: date, prices: PriceList
) -> DaySpend:
    on_the_day = CustomerSession.business_date == business_date
    by_purpose: dict[str, Cost] = {}
    by_channel: dict[Channel, Cost] = {}
    calls = await db.execute(
        select(AgentEvent.payload, CustomerSession.channel)
        .join(CustomerSession, AgentEvent.session_id == CustomerSession.id)
        .where(on_the_day, AgentEvent.kind == AgentEventKind.MODEL_CALL)
    )
    for payload, channel in calls:
        cost = call_cost(payload, prices)
        _add(by_purpose, _purpose(payload), cost)
        _add(by_channel, channel, cost)
    summaries = await db.execute(
        select(SessionSummary)
        .join(CustomerSession, SessionSummary.session_id == CustomerSession.id)
        .where(on_the_day)
    )
    for summary in summaries.scalars():
        _add(by_purpose, SUMMARY, _summary_cost(summary, prices))

    conversations = await db.scalar(
        select(func.count(distinct(AgentEvent.session_id)))
        .join(CustomerSession, AgentEvent.session_id == CustomerSession.id)
        .where(on_the_day, AgentEvent.kind == AgentEventKind.USER_MESSAGE)
    )
    orders = await db.scalar(
        select(func.count(Order.id))
        .join(CustomerSession, Order.session_id == CustomerSession.id)
        .where(on_the_day)
    )
    total = sum(by_purpose.values(), Cost())
    return DaySpend(
        business_date=business_date,
        conversations=conversations or 0,
        orders=orders or 0,
        total=total,
        by_purpose=by_purpose,
        by_channel=by_channel,
        per_conversation_micro_usd=_per(total, conversations),
        per_order_micro_usd=_per(total, orders),
        prices_checked_on=prices.checked_on,
        prices_source=prices.source,
    )


def _summary_cost(summary: SessionSummary | None, prices: PriceList) -> Cost:
    if summary is None or summary.model is None:
        # Never written, or written by code: no model was asked.
        return Cost()
    return call_cost(summary_call(summary), prices)


def _purpose(payload: Mapping[str, Any]) -> str:
    return str(payload.get("purpose") or TURN)


def _add[K](costs: dict[K, Cost], key: K, cost: Cost) -> None:
    costs[key] = costs.get(key, Cost()) + cost


def _per(total: Cost, count: int | None) -> int | None:
    return round(total.micro_usd / count) if count else None
