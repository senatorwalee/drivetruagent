import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from drivetruagent.agent import timekeeper
from drivetruagent.api import (
    actions,
    conversation,
    health,
    inventory,
    menu,
    offers,
    sessions,
)
from drivetruagent.api.errors import register_error_handlers
from drivetruagent.config import get_settings
from drivetruagent.db.session import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine and start the timekeeper at startup; stop
    the timekeeper and close the pool at shutdown."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    sweeping: asyncio.Task[None] | None = None
    if settings.timekeeper_interval_seconds > 0:
        # Nudges silent customers, ends sessions that have timed out, and
        # tidies up expired stock holds. See agent/timekeeper.py.
        sweeping = asyncio.create_task(
            timekeeper.run_forever(
                app.state.session_factory,
                interval_seconds=settings.timekeeper_interval_seconds,
                timers=settings.session_timers,
                tax_rate=settings.tax_rate,
            )
        )
    yield
    if sweeping is not None:
        sweeping.cancel()
        with suppress(asyncio.CancelledError):
            await sweeping
    await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application. Tests call this to get a fresh app."""
    app = FastAPI(title="Drive-through agent", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(actions.router)
    app.include_router(conversation.router)
    app.include_router(menu.router)
    app.include_router(inventory.router)
    app.include_router(offers.router)
    register_error_handlers(app)
    return app


app = create_app()
