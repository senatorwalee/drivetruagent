"""Fixtures for tests that need a real database.

One Postgres container is started for the whole test run and migrated once.
Each test then works inside a transaction that is rolled back at the end, so
every test starts from the same clean database.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from drivetruagent.db.base import Base
from drivetruagent.db.session import get_session
from drivetruagent.main import create_app
from drivetruagent.seed import load_menu, seed_menu

POSTGRES_IMAGE = "pgvector/pgvector:pg17"
REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MENU_PATH = REPO_ROOT / "data" / "menu.json"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start a throwaway Postgres and return its URL. Removed when the run ends."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> str:
    """Apply the real migrations, so tests see the same schema production would."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(config, "head")
    return postgres_url


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back when the test finishes.

    The engine is created per test with NullPool: sharing one async engine
    across tests ties its connections to an event loop that has since closed.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        # create_savepoint lets code under test call commit() without ending
        # the outer transaction that this fixture rolls back.
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client for the app, with requests using the test's session."""
    app = create_app()

    async def use_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = use_test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def menu(db_session: AsyncSession) -> None:
    """The real menu from data/menu.json, loaded for this test only."""
    await seed_menu(db_session, load_menu(MENU_PATH))


@pytest.fixture
async def committing_sessions(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions that really commit, each on its own connection.

    For tests where several transactions must run at the same time, which the
    rollback-based db_session cannot do. Every table is emptied afterwards, so
    other tests still start from a clean database.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)

    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()
