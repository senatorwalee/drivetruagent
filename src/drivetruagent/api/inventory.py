"""Stock endpoints, for staff and for setting up a demo."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from drivetruagent.api.deps import SessionDep
from drivetruagent.api.errors import NOT_FOUND, error_response
from drivetruagent.ordering import inventory
from drivetruagent.ordering.errors import UnknownItem

router = APIRouter(prefix="/inventory", tags=["inventory"])


class StockLevelView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_code: str
    # Units physically there.
    on_hand: int
    # Units held by sessions that are still ordering.
    reserved: int
    # What a new customer could have: on hand minus reserved.
    available: int


class SetStockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_hand: int = Field(ge=0)


@router.get("", summary="Stock levels")
async def read_stock(db: SessionDep) -> list[StockLevelView]:
    """Every stock-tracked item. Items not listed are not tracked and are
    treated as unlimited."""
    return [
        StockLevelView.model_validate(level)
        for level in await inventory.stock_levels(db)
    ]


@router.put(
    "/{item_code}",
    responses=NOT_FOUND,
    response_model=StockLevelView,
    summary="Set how many of an item are on hand",
)
async def set_stock(
    item_code: str, body: SetStockRequest, db: SessionDep
) -> StockLevelView | JSONResponse:
    """Record a count, starting to track the item if it was not tracked.

    Setting stock below what sessions already hold does not cancel their
    holds. Those orders are checked again when they are submitted.
    """
    try:
        level = await inventory.set_on_hand(db, item_code, body.on_hand)
    except UnknownItem as error:
        # The item is the thing addressed by the URL, so this is "not found".
        return error_response(error, 404)
    await db.commit()
    return StockLevelView.model_validate(level)
