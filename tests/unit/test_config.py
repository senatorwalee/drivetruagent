from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from drivetruagent.config import Settings

DATABASE_URL = "postgresql+psycopg://user:password@localhost:5433/example"


def make_settings(**values: Any) -> Settings:
    """Settings from the given values alone: no environment, no .env file."""
    values = {"database_url": DATABASE_URL, "_env_file": None, **values}
    return Settings(**values)


def test_environment_variable_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    # _env_file=None keeps the developer's local .env out of the test.
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.database_url == DATABASE_URL
    assert settings.log_level == "DEBUG"


def test_missing_database_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


# --- The restaurant's own date ----------------------------------------------


def test_the_restaurant_is_in_toronto_by_default() -> None:
    assert make_settings().restaurant_timezone == "America/Toronto"


def test_the_business_date_is_the_date_where_the_restaurant_is() -> None:
    """One instant, two calendar dates: 03:30 UTC on the 13th is still the
    evening of the 12th in Toronto, and that is the day whose special applies."""
    instant = datetime(2026, 10, 13, 3, 30, tzinfo=UTC)

    assert make_settings().business_date(instant) == date(2026, 10, 12)
    assert make_settings(restaurant_timezone="UTC").business_date(instant) == date(
        2026, 10, 13
    )
    assert make_settings(restaurant_timezone="Asia/Tokyo").business_date(
        instant
    ) == date(2026, 10, 13)


def test_the_business_date_defaults_to_now() -> None:
    before = datetime.now(UTC).date()
    today = make_settings(restaurant_timezone="UTC").business_date()

    # A range, so the test holds even if midnight passes while it runs.
    assert before <= today <= datetime.now(UTC).date()


def test_an_unknown_timezone_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        make_settings(restaurant_timezone="Mars/Olympus_Mons")


def test_the_specials_cache_lasts_a_minute_by_default() -> None:
    assert make_settings().specials_cache_ttl == timedelta(seconds=60)
    assert make_settings(specials_cache_ttl_seconds=0).specials_cache_ttl == timedelta(
        0
    )


# --- Session timers ---------------------------------------------------------


def test_the_timers_default_to_what_the_brief_asks_for() -> None:
    timers = make_settings().session_timers

    assert timers.nudge_after == timedelta(seconds=20)
    assert timers.close_after == timedelta(seconds=20)
    assert timers.max_duration == timedelta(minutes=3)
    assert make_settings().timekeeper_interval_seconds == 1.0


def test_the_timers_can_be_changed_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILENCE_NUDGE_SECONDS", "45")
    monkeypatch.setenv("SESSION_MAX_SECONDS", "600")
    monkeypatch.setenv("TIMEKEEPER_INTERVAL_SECONDS", "0")

    settings = make_settings()

    assert settings.session_timers.nudge_after == timedelta(seconds=45)
    assert settings.session_timers.max_duration == timedelta(minutes=10)
    assert settings.timekeeper_interval_seconds == 0


@pytest.mark.parametrize(
    "field", ["silence_nudge_seconds", "silence_close_seconds", "session_max_seconds"]
)
def test_a_timer_of_zero_seconds_is_rejected(field: str) -> None:
    """It would fire on every sweep."""
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: 0})
