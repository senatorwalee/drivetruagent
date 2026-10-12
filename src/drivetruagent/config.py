from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    environment: Literal["local", "test", "ci"] = "local"
    log_level: str = "INFO"
    # Sales tax as a fraction of the subtotal. The default is Quebec's combined
    # GST and QST: 5% + 9.975%.
    tax_rate: Decimal = Field(default=Decimal("0.14975"), ge=0, lt=1)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, reading the environment only on the first call."""
    # Pyright cannot see that database_url is filled from the environment.
    return Settings()  # pyright: ignore[reportCallIssue]
