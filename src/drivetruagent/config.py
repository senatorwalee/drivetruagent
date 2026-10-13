from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from drivetruagent.policy.decisions import PolicyLimits


class Settings(BaseSettings):
    """Application settings, read from environment variables and a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    environment: Literal["local", "test", "ci"] = "local"
    log_level: str = "INFO"
    # Sales tax as a fraction of the subtotal. The default is Ontario's HST.
    tax_rate: Decimal = Field(default=Decimal("0.13"), ge=0, lt=1)

    # Where the restaurant is. "Today's special" means today there, whatever
    # timezone the server runs in.
    restaurant_timezone: str = "America/Toronto"
    # How long the day's specials are served from memory before being read
    # again. Covers changes this process is not told about, such as a re-seed.
    specials_cache_ttl_seconds: int = Field(default=60, ge=0)

    # How long stock stays held for a session with no activity. It must
    # outlast the longest session plus the time a handoff takes.
    reservation_ttl_seconds: int = Field(default=600, ge=1)

    # The agent. Without a key the ordering API still works; only the
    # conversation endpoint is unavailable.
    # "openai" talks to a real model. "demo" plays one fixed conversation with
    # no key and no cost; its tool calls still run through the real backend.
    agent_model: Literal["openai", "demo"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_text_model: str = "gpt-6-luna"
    restaurant_name: str = "Maple Lane Burgers"
    # How many times the model may ask for tools within one customer turn.
    agent_max_tool_rounds: int = Field(default=8, ge=1)

    # Policy limits. More than this many of one item on a line is unusual,
    # and the customer is asked to confirm it.
    line_quantity_confirm_above: int = Field(default=10, ge=1)
    # More than this many is extreme: once confirmed, a person takes over.
    line_quantity_escalate_above: int = Field(default=25, ge=1)
    # The same applies to a cart holding more than this many items in total.
    cart_items_escalate_above: int = Field(default=50, ge=1)

    @field_validator("restaurant_timezone")
    @classmethod
    def check_timezone_exists(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown timezone: {name!r}") from error
        return name

    @model_validator(mode="after")
    def check_limits_are_in_order(self) -> Self:
        if self.line_quantity_escalate_above <= self.line_quantity_confirm_above:
            raise ValueError(
                "line_quantity_escalate_above must be greater than "
                "line_quantity_confirm_above"
            )
        return self

    def business_date(self, now: datetime | None = None) -> date:
        """The restaurant's calendar date at `now` (default: this moment)."""
        zone = ZoneInfo(self.restaurant_timezone)
        return (now.astimezone(zone) if now else datetime.now(zone)).date()

    @property
    def specials_cache_ttl(self) -> timedelta:
        return timedelta(seconds=self.specials_cache_ttl_seconds)

    @property
    def reservation_ttl(self) -> timedelta:
        return timedelta(seconds=self.reservation_ttl_seconds)

    @property
    def policy_limits(self) -> PolicyLimits:
        return PolicyLimits(
            line_quantity_confirm_above=self.line_quantity_confirm_above,
            line_quantity_escalate_above=self.line_quantity_escalate_above,
            cart_items_escalate_above=self.cart_items_escalate_above,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the settings, reading the environment only on the first call."""
    # Pyright cannot see that database_url is filled from the environment.
    return Settings()  # pyright: ignore[reportCallIssue]
