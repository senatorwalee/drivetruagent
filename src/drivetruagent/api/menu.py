"""Menu endpoints. Read only."""

from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from drivetruagent.api.deps import SessionDep
from drivetruagent.api.errors import NOT_FOUND, error_response
from drivetruagent.enums import Language
from drivetruagent.ordering import menu
from drivetruagent.ordering.errors import UnknownItem
from drivetruagent.ordering.views import ItemDetails, MenuMatch, MenuView

router = APIRouter(prefix="/menu", tags=["menu"])

LanguageParam = Annotated[
    Language, Query(description="The language names are returned in.")
]


@router.get("", summary="The whole menu")
async def read_menu(db: SessionDep, language: LanguageParam = Language.EN) -> MenuView:
    """Every available item, grouped by category, in display order."""
    return await menu.get_menu(db, language)


@router.get("/search", summary="Find items matching what a customer said")
async def search(
    db: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    language: LanguageParam = Language.EN,
    limit: Annotated[int, Query(ge=1, le=20)] = menu.DEFAULT_SEARCH_LIMIT,
) -> list[MenuMatch]:
    """Ranked matches, best first; an empty list if nothing matches.

    Names and aliases in every language are searched. Several results with
    the same score mean the customer was vague and should be asked.
    """
    return await menu.search_menu(db, q, language, limit)


@router.get(
    "/items/{item_code}",
    responses=NOT_FOUND,
    response_model=ItemDetails,
    summary="One item with its choices",
)
async def read_item(
    item_code: str, db: SessionDep, language: LanguageParam = Language.EN
) -> ItemDetails | JSONResponse:
    try:
        return await menu.get_item_details(db, item_code, language)
    except UnknownItem as error:
        # Here the item is the thing addressed by the URL, so a missing one is
        # "not found". As part of a cart request it would be a refused request.
        return error_response(error, status.HTTP_404_NOT_FOUND)
