import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from drivetruagent.agent import summarizer, timekeeper
from drivetruagent.agent.factory import summary_model_from_settings
from drivetruagent.api import (
    actions,
    callers,
    conversation,
    costs,
    handoffs,
    health,
    insights,
    inventory,
    menu,
    menu_admin,
    offers,
    phone,
    pickups,
    sessions,
    summaries,
    voice,
    web_phone,
)
from drivetruagent.api.errors import register_error_handlers
from drivetruagent.config import get_settings
from drivetruagent.db.session import create_engine, create_session_factory
from drivetruagent.telemetry import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine and start the timekeeper at startup; stop
    the timekeeper and close the pool at shutdown."""
    settings = get_settings()
    # Off unless TRACING_ENDPOINT is set.
    tracer_provider = setup_tracing(settings)
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    sweeping: asyncio.Task[None] | None = None
    if settings.timekeeper_interval_seconds > 0:
        # Nudges silent customers, ends sessions that have timed out, tidies
        # up expired stock holds, and forgets old callers. See
        # agent/timekeeper.py.
        sweeping = asyncio.create_task(
            timekeeper.run_forever(
                app.state.session_factory,
                interval_seconds=settings.timekeeper_interval_seconds,
                timers=settings.session_timers,
                tax_rate=settings.tax_rate,
                caller_retention=settings.caller_retention,
            )
        )
    summarising: asyncio.Task[None] | None = None
    if settings.summary_interval_seconds > 0:
        # Writes a note for staff about each session that ends. See
        # agent/summarizer.py.
        summarising = asyncio.create_task(
            summarizer.run_forever(
                app.state.session_factory,
                summary_model_from_settings(settings),
                interval_seconds=settings.summary_interval_seconds,
                settings=settings.summary_settings,
            )
        )
    yield
    live_calls = getattr(app.state, "live_calls", None)
    if live_calls is not None:
        await live_calls.close_all()
    for task in (sweeping, summarising):
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    await engine.dispose()
    if tracer_provider is not None:
        # Sends whatever spans are still waiting.
        tracer_provider.shutdown()


def create_app() -> FastAPI:
    """Build the FastAPI application. Tests call this to get a fresh app."""
    app = FastAPI(title="Drive-through agent", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(actions.router)
    app.include_router(conversation.router)
    app.include_router(menu.router)
    app.include_router(menu_admin.router)
    app.include_router(inventory.router)
    app.include_router(offers.router)
    app.include_router(handoffs.router)
    app.include_router(voice.router)
    app.include_router(phone.router)
    app.include_router(web_phone.router)
    app.include_router(pickups.router)
    app.include_router(callers.router)
    app.include_router(insights.router)
    app.include_router(summaries.router)
    app.include_router(costs.router)
    register_error_handlers(app)
    return app


app = create_app()
