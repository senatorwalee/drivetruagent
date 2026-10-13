from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from drivetruagent.api.deps import get_session
from drivetruagent.config import get_settings
from drivetruagent.main import create_app

# Nothing listens on port 1, so connections are refused immediately.
UNREACHABLE_URL = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere"


async def test_ready_returns_ok_when_database_is_up(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_503_when_database_is_unreachable() -> None:
    engine = create_async_engine(UNREACHABLE_URL, poolclass=NullPool)

    async def use_broken_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine) as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = use_broken_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/health/ready")
        live = await client.get("/health/live")

    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable"}
    # Liveness must not depend on the database.
    assert live.status_code == 200


async def test_ready_works_through_the_real_startup_path(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dependency override here: the lifespan builds the engine from settings."""
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    get_settings.cache_clear()
    app = create_app()

    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/health/ready")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
