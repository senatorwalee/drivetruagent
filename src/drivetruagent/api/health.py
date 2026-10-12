from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


@router.get("/live")
async def live() -> HealthResponse:
    """Report that the process is running. Does not touch the database."""
    return HealthResponse(status="ok")
