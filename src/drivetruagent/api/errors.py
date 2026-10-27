"""Turns ordering errors into HTTP responses.

Every expected failure has the same JSON shape, built straight from the
OrderingError. A caller reacts to `error.code`, never to the message text.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.ordering.errors import OrderingError

# The thing asked for does not exist.
_NOT_FOUND = {
    "session_not_found",
    "unknown_cart_line",
    "order_not_found",
    "pending_action_not_found",
    "handoff_not_found",
    "no_voice_call",
    "no_photo",
}
# Turned off on this server.
_FORBIDDEN = {"typed_caller_id_off"}
# The request is well formed, but the current state does not allow it.
_CONFLICT = {
    "lane_busy",
    "session_ended",
    "item_unavailable",
    "option_unavailable",
    "empty_cart",
    "insufficient_stock",
    "handoff_resolved",
    "session_not_handed_off",
    "voice_call_in_progress",
    "duplicate_item_name",
}


AGENT_UNAVAILABLE = "agent_unavailable"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    error: ErrorBody


def status_for(error: OrderingError) -> int:
    if error.code in _NOT_FOUND:
        return status.HTTP_404_NOT_FOUND
    if error.code in _CONFLICT:
        return status.HTTP_409_CONFLICT
    if error.code in _FORBIDDEN:
        return status.HTTP_403_FORBIDDEN
    # Everything else asks for something the menu or the rules do not allow.
    return status.HTTP_422_UNPROCESSABLE_CONTENT


def error_response(
    error: OrderingError, status_code: int | None = None
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=error.code, message=error.message, details=error.details)
    )
    return JSONResponse(
        status_code=status_code or status_for(error),
        content=body.model_dump(mode="json"),
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OrderingError)
    async def handle_ordering_error(  # pyright: ignore[reportUnusedFunction]
        request: Request, error: OrderingError
    ) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(ModelUnavailable)
    async def handle_model_unavailable(  # pyright: ignore[reportUnusedFunction]
        request: Request, error: ModelUnavailable
    ) -> JSONResponse:
        # The ordering API keeps working; only the conversation is down.
        body = ErrorResponse(
            error=ErrorBody(code=AGENT_UNAVAILABLE, message=str(error), details={})
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body.model_dump(mode="json"),
        )


# For the `responses=` argument of a route, so /docs shows the error shapes.
type Responses = dict[int | str, dict[str, Any]]

NOT_FOUND: Responses = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}
CONFLICT: Responses = {status.HTTP_409_CONFLICT: {"model": ErrorResponse}}
FORBIDDEN: Responses = {status.HTTP_403_FORBIDDEN: {"model": ErrorResponse}}
REFUSED: Responses = {status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}}
UNAVAILABLE: Responses = {status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse}}
