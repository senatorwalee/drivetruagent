from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from drivetruagent.agent.voice import VoiceSettings
from drivetruagent.ordering.timers import TimerSettings
from drivetruagent.policy.decisions import PolicyLimits


class Settings(BaseSettings):
    """Application settings, read from environment variables and a local .env file."""

    # env_ignore_empty: a blank line such as `OPENAI_API_KEY=` (as in
    # .env.example) means "not set", not an empty key.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

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

    # Session timers. After this much silence the customer is asked whether
    # they are still there.
    silence_nudge_seconds: int = Field(default=20, ge=1)
    # If they then stay silent this much longer, the session is closed.
    silence_close_seconds: int = Field(default=20, ge=1)
    # A session that has run this long is handed to a person.
    session_max_seconds: int = Field(default=180, ge=1)
    # How often the background task checks the timers. 0 turns it off; the
    # length limit is then still checked whenever the customer speaks.
    timekeeper_interval_seconds: float = Field(default=1.0, ge=0)

    # The agent. Without a key the ordering API still works; only the
    # conversation endpoint is unavailable.
    # "openai" and "gemini" talk to a real model. "demo" plays one fixed
    # conversation with no key and no cost; its tool calls still run through
    # the real backend.
    agent_model: Literal["openai", "gemini", "demo"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_text_model: str = "gpt-6-luna"
    # How hard the model reasons before each answer. "none" is fastest but
    # jumped ahead: it asked to confirm the order before the customer was done
    # in 11 of 20 confirmations in the evals, against 1 of 13 at "low". The
    # guardrail classifier, which only picks a label, always uses "none".
    openai_reasoning_effort: Literal["none", "low", "medium", "high"] | None = "low"
    # Guardrails: how many strikes before the session ends (abuse, attempts
    # to get round the rules) or goes to a person (off topic, getting
    # nowhere). The classifier call can be switched off; the rules cannot.
    guardrail_abuse_strikes: int = Field(default=3, ge=1)
    guardrail_confusion_strikes: int = Field(default=3, ge=1)
    guardrail_classifier: bool = True
    # True: classify each message before the agent starts, so a blocked one
    # never reaches the model at all. False (default): both start together,
    # saving about a second and a half per turn; a blocked message's answer
    # is cancelled and never used, and no tool runs.
    guardrail_check_first: bool = False

    # Voice, through OpenAI Realtime. Needs OPENAI_API_KEY whichever text
    # model is chosen. See agent/voice.py.
    voice_model: str = "gpt-realtime-2.1-mini"
    voice_name: str = "marin"
    voice_transcription_model: str = "gpt-4o-mini-transcribe"
    # Google's Gemini API has a free tier. See agent/gemini_model.py.
    gemini_api_key: SecretStr | None = None
    gemini_text_model: str = "gemini-2.5-flash-lite"
    restaurant_name: str = "Maple Lane Burgers"
    # Knowledge search. With OPENAI_API_KEY, documents are embedded with this
    # model; without one, with the local hashing embedder. `make ingest`
    # indexes with both, so either can search.
    embedding_model: str = "text-embedding-3-small"
    # The cosine similarity a passage needs to count as evidence. None: the
    # embedder's own threshold.
    knowledge_min_score: float | None = Field(default=None, ge=0, le=1)
    knowledge_results: int = Field(default=3, ge=1, le=10)
    knowledge_dir: str = "knowledge"
    # Tracing to Arize Phoenix. Unset: off. With `make phoenix` running, set
    # it to http://localhost:6006/v1/traces. See telemetry.py.
    tracing_endpoint: str | None = None
    tracing_project: str = "drivetruagent"
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
    def voice_settings(self) -> VoiceSettings:
        return VoiceSettings(
            model=self.voice_model,
            voice=self.voice_name,
            transcription_model=self.voice_transcription_model,
        )

    @property
    def session_timers(self) -> TimerSettings:
        return TimerSettings(
            nudge_after=timedelta(seconds=self.silence_nudge_seconds),
            close_after=timedelta(seconds=self.silence_close_seconds),
            max_duration=timedelta(seconds=self.session_max_seconds),
        )

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
