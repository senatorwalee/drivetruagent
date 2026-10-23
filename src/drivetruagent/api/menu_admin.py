"""Menu editing endpoints, for the menu page: add, change, take off, photos.

Changes reach the agent from the next session, whose instructions are built
from the menu when it starts.
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from drivetruagent.api.deps import SessionDep, SpecialsCacheDep
from drivetruagent.api.errors import CONFLICT, NOT_FOUND, REFUSED, error_response
from drivetruagent.ordering import menu_admin
from drivetruagent.ordering.errors import PhotoNotAccepted, UnknownItem
from drivetruagent.ordering.menu_admin import (
    AdminItemView,
    AdminMenuView,
    ItemChanges,
    NewItem,
)
from drivetruagent.ordering.photos import MAX_PHOTO_BYTES, SUPPORTED_TYPES

router = APIRouter(prefix="/admin/menu", tags=["menu admin"])


@router.get("", summary="Every item, for editing")
async def read_admin_menu(db: SessionDep) -> AdminMenuView:
    """Items taken off the menu are included, marked `is_active: false`."""
    return await menu_admin.read_admin_menu(db)


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
    responses={**CONFLICT, **REFUSED},
    response_model=AdminItemView,
    summary="Add an item",
)
async def create_item(
    body: NewItem, db: SessionDep, cache: SpecialsCacheDep
) -> AdminItemView:
    """Added at the end of its category. Its code is made from the English
    name. Its allergens start unreviewed, so the agent will not discuss them."""
    item = await menu_admin.create_item(db, body)
    await db.commit()
    cache.invalidate()
    return item


@router.patch(
    "/items/{item_code}",
    responses={**NOT_FOUND, **CONFLICT, **REFUSED},
    response_model=AdminItemView,
    summary="Change an item, or take it off the menu",
)
async def update_item(
    item_code: str, body: ItemChanges, db: SessionDep, cache: SpecialsCacheDep
) -> AdminItemView | JSONResponse:
    """Only the fields sent are changed. `is_active: false` takes the item
    off the menu; past orders keep it, and `true` puts it back."""
    try:
        item = await menu_admin.update_item(db, item_code, body)
    except UnknownItem as error:
        return error_response(error, status.HTTP_404_NOT_FOUND)
    await db.commit()
    # Today's specials are cached with their items; one may just have gone.
    cache.invalidate()
    return item


@router.put(
    "/items/{item_code}/photo",
    responses={**NOT_FOUND, **REFUSED},
    response_model=AdminItemView,
    summary="Set or replace an item's photo",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                content_type: {"schema": {"type": "string", "format": "binary"}}
                for content_type in SUPPORTED_TYPES
            },
        }
    },
)
async def set_photo(
    item_code: str, request: Request, db: SessionDep
) -> AdminItemView | JSONResponse:
    """The body is the image itself: JPEG, PNG or WebP, at most 5 MB. Its type
    is read from its bytes, whatever Content-Type says."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_PHOTO_BYTES:
            # Stop reading: there is no point taking in the rest.
            return error_response(
                PhotoNotAccepted("too_large", "The photo is larger than 5 MB."),
                status.HTTP_413_CONTENT_TOO_LARGE,
            )
    try:
        item = await menu_admin.set_photo(db, item_code, bytes(data))
    except UnknownItem as error:
        return error_response(error, status.HTTP_404_NOT_FOUND)
    await db.commit()
    return item


@router.delete(
    "/items/{item_code}/photo",
    responses=NOT_FOUND,
    response_model=AdminItemView,
    summary="Remove an item's photo",
)
async def remove_photo(item_code: str, db: SessionDep) -> AdminItemView | JSONResponse:
    """The page then shows a placeholder for the item's category."""
    try:
        item = await menu_admin.remove_photo(db, item_code)
    except UnknownItem as error:
        return error_response(error, status.HTTP_404_NOT_FOUND)
    await db.commit()
    return item
