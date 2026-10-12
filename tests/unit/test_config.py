import pytest
from pydantic import ValidationError

from drivetruagent.config import Settings

DATABASE_URL = "postgresql+psycopg://user:password@localhost:5433/example"


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
