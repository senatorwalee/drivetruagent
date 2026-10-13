"""Dependencies shared by the API routes."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.config import Settings, get_settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request, always closed afterwards.

    Closing a session discards anything that was not committed, so a request
    that fails part-way leaves nothing behind.
    """
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Optional header on requests that change state. A repeat with the same key
# gets the first response back and nothing runs twice.
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        description="Send the same value when retrying a request.",
    ),
]
SettingsDep = Annotated[Settings, Depends(get_settings)]
