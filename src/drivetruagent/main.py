from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from drivetruagent.api import actions, conversation, health, menu, sessions
from drivetruagent.api.errors import register_error_handlers
from drivetruagent.config import get_settings
from drivetruagent.db.session import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine at startup and close its pool at shutdown."""
    settings = get_settings()
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application. Tests call this to get a fresh app."""
    app = FastAPI(title="Drive-through agent", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(actions.router)
    app.include_router(conversation.router)
    app.include_router(menu.router)
    register_error_handlers(app)
    return app


app = create_app()
