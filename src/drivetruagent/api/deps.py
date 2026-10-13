"""Dependencies shared by the API routes."""

from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from drivetruagent.cache import TtlCache
from drivetruagent.config import Settings, get_settings
from drivetruagent.ordering.offers import SpecialsCache


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


def get_business_date(settings: SettingsDep) -> date:
    """Today's date where the restaurant is. Tests replace this to pick a day."""
    return settings.business_date()


BusinessDateDep = Annotated[date, Depends(get_business_date)]


def get_specials_cache(request: Request, settings: SettingsDep) -> SpecialsCache:
    """The app's one cache of the day's specials, created on first use.

    It hangs off the app, not a module, so every app (and so every test)
    starts with an empty one.
    """
    cache: SpecialsCache | None = getattr(request.app.state, "specials_cache", None)
    if cache is None:
        cache = TtlCache(settings.specials_cache_ttl)
        request.app.state.specials_cache = cache
    return cache


SpecialsCacheDep = Annotated[SpecialsCache, Depends(get_specials_cache)]
