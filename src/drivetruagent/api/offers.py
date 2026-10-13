"""What makes an order cheaper: combos and the day's specials.

There is nothing here to apply a discount with. Discounts are worked out
from the cart; these endpoints say what the rules are, and let staff end a
special early.
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from drivetruagent.api.deps import BusinessDateDep, SessionDep, SpecialsCacheDep
from drivetruagent.api.errors import NOT_FOUND, error_response
from drivetruagent.api.menu import LanguageParam
from drivetruagent.enums import Language
from drivetruagent.ordering import offers
from drivetruagent.ordering.errors import UnknownSpecial
from drivetruagent.ordering.views import ComboView, SpecialView

router = APIRouter(tags=["offers"])


class SetSpecialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class SpecialStatus(BaseModel):
    code: str
    is_active: bool


@router.get("/combos", summary="The combo deals")
async def read_combos(
    db: SessionDep, language: LanguageParam = Language.EN
) -> list[ComboView]:
    """The active combos, highest priority first.

    A cart earns a combo by holding one item from each of its slots. The
    saving then shows up in the cart's `discounts` without anything being
    asked for.
    """
    return await offers.list_combos(db, language)


@router.get("/specials", summary="Today's specials")
async def read_specials(
    db: SessionDep,
    today: BusinessDateDep,
    cache: SpecialsCacheDep,
    language: LanguageParam = Language.EN,
) -> list[SpecialView]:
    """The specials running today, where the restaurant is.

    The list is served from a short-lived cache; `in_stock` is read live. A
    session keeps the specials of the day it opened on, so a cart is priced
    from the session's own date, not from this.
    """
    return await offers.todays_specials(db, today, language, cache=cache)


@router.put(
    "/specials/{code}",
    responses=NOT_FOUND,
    response_model=SpecialStatus,
    summary="End a special early, or bring it back",
)
async def set_special(
    code: str, body: SetSpecialRequest, db: SessionDep, cache: SpecialsCacheDep
) -> SpecialStatus | JSONResponse:
    """For staff. Takes effect on the next read and the next cart change."""
    try:
        await offers.set_special_active(db, code, body.is_active)
    except UnknownSpecial as error:
        # The special is the thing addressed by the URL, so this is "not found".
        return error_response(error, status.HTTP_404_NOT_FOUND)
    # Commit first, then forget. The other way round, a reader could refill
    # the cache with the old list in the gap before the change is saved.
    await db.commit()
    cache.invalidate()
    return SpecialStatus(code=code, is_active=body.is_active)
