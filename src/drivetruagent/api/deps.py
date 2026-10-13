"""Dependencies shared by the API routes."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
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
SettingsDep = Annotated[Settings, Depends(get_settings)]
