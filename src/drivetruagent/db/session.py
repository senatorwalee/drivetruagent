"""The database engine and session factory. Knows nothing about the web layer."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

CONNECT_TIMEOUT_SECONDS = 5


def create_engine(database_url: str) -> AsyncEngine:
    """Build the engine. No connection is opened until the first query."""
    return create_async_engine(
        database_url,
        # Check a pooled connection is alive before using it, so the app
        # recovers on its own after the database restarts.
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False keeps loaded objects usable after a commit; the
    # default would trigger a lazy reload, which fails in async code.
    return async_sessionmaker(engine, expire_on_commit=False)
