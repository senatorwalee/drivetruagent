"""What the model has cost, worked out from logged tokens and the price list.

    GET /costs/today          the day: total, per conversation, per order
    GET /sessions/{id}/cost   one session, as it runs

Prices are in data/model_prices.json (MODEL_PRICES_PATH). There is no
sign-in: this is a local demo.
"""

import uuid
from datetime import date

from fastapi import APIRouter

from drivetruagent.agent import spend
from drivetruagent.agent.costs import PriceList
from drivetruagent.agent.spend import DaySpend, SessionSpend
from drivetruagent.api.deps import BusinessDateDep, SessionDep, SettingsDep
from drivetruagent.api.errors import NOT_FOUND, UNAVAILABLE
from drivetruagent.config import Settings
from drivetruagent.ordering.errors import NoPrices

router = APIRouter(tags=["costs"])


def _prices(settings: Settings) -> PriceList:
    prices = settings.model_prices
    if prices is None:
        raise NoPrices(settings.model_prices_path)
    return prices


@router.get(
    "/costs/today",
    responses=UNAVAILABLE,
    summary="What the model cost today",
)
async def read_day(
    db: SessionDep,
    settings: SettingsDep,
    today: BusinessDateDep,
    day: date | None = None,
) -> DaySpend:
    """Every model call made in the restaurant's day (or `day`), the notes
    written after each session included, with cost per conversation and
    per order."""
    return await spend.day_spend(db, day or today, _prices(settings))


@router.get(
    "/sessions/{session_id}/cost",
    responses=NOT_FOUND | UNAVAILABLE,
    summary="What one session's model calls cost",
)
async def read_session(
    session_id: uuid.UUID, db: SessionDep, settings: SettingsDep
) -> SessionSpend:
    """So far, for a session still going; the live screen asks after each turn."""
    return await spend.session_spend(db, session_id, _prices(settings))
