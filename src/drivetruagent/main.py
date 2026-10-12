from fastapi import FastAPI

from drivetruagent.api import health


def create_app() -> FastAPI:
    """Build the FastAPI application. Tests call this to get a fresh app."""
    app = FastAPI(title="Drive-through agent")
    app.include_router(health.router)
    return app


app = create_app()
