from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    environment: Literal["local", "test", "ci"] = "local"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, reading the environment only on the first call."""
    # Pyright cannot see that database_url is filled from the environment.
    return Settings()  # pyright: ignore[reportCallIssue]
