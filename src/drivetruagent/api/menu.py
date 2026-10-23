"""Menu endpoints. Read only; changes go through api/menu_admin.py."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse

from drivetruagent.api.deps import SessionDep
from drivetruagent.api.errors import NOT_FOUND, error_response
from drivetruagent.enums import Language
from drivetruagent.ordering import allergen_matrix, menu, menu_admin
from drivetruagent.ordering.allergen_matrix import AllergenReport
from drivetruagent.ordering.errors import NoPhoto, UnknownItem
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


@router.get(
    "/items/{item_code}/allergens",
    responses=NOT_FOUND,
    response_model=AllergenReport,
    summary="One item's allergens",
)
async def read_item_allergens(
    item_code: str, db: SessionDep, language: LanguageParam = Language.EN
) -> AllergenReport | JSONResponse:
    """What the item contains and may contain with its default choices, and
    which other choices would add an allergen. From the allergen matrix.

    `verified` false means the item's allergens were never reviewed, and the
    lists say nothing.
    """
    try:
        return await allergen_matrix.item_allergens(db, item_code, None, None, language)
    except UnknownItem as error:
        return error_response(error, status.HTTP_404_NOT_FOUND)


@router.get(
    "/items/{item_code}/photo",
    responses={
        **NOT_FOUND,
        status.HTTP_200_OK: {"content": {"image/jpeg": {}}, "description": "The photo"},
    },
    response_class=Response,
    summary="One item's photo",
)
async def read_item_photo(
    item_code: str, db: SessionDep, v: str | None = None
) -> Response:
    """The image itself. Asked for with `v`, the version the menu page was
    given, it can be cached for good: a new photo comes with a new version."""
    try:
        photo = await menu_admin.read_photo(db, item_code)
    except (UnknownItem, NoPhoto) as error:
        return error_response(error, status.HTTP_404_NOT_FOUND)
    fresh = v is not None and photo.sha256.startswith(v)
    return Response(
        content=photo.data,
        media_type=photo.content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable"
            if fresh
            else "no-cache",
            "ETag": f'"{photo.sha256}"',
        },
    )
