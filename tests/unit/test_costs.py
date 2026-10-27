"""What model calls cost, from logged tokens. No database, no network."""

from datetime import date
from decimal import Decimal

import pytest

from drivetruagent.agent.costs import (
    Cost,
    Rates,
    call_cost,
    cost_of,
    load_prices,
    parse_prices,
)
from drivetruagent.config import Settings

PRICES = parse_prices(
    {
        "checked_on": "2026-10-27",
        "source": "https://example.test/pricing",
        "models": {
            "gpt-6-luna": {
                "text": {"input": "0.10", "cached_input": "0.01", "output": "0.50"}
            },
            "gpt-realtime-2.1": {
                "text": {"input": "4.00", "cached_input": "0.40", "output": "24.00"}
            },
            "gpt-realtime-2.1-mini": {
                "text": {"input": "0.60", "cached_input": "0.06", "output": "2.40"},
                "audio": {"input": "10.00", "cached_input": "0.30", "output": "20.00"},
            },
        },
    }
)


def test_the_price_list_is_read_with_its_date_and_source() -> None:
    assert PRICES.checked_on == date(2026, 10, 27)
    luna = PRICES.price_for("gpt-6-luna")
    assert luna is not None
    assert luna.text == Rates(Decimal("0.10"), Decimal("0.01"), Decimal("0.50"))
    assert luna.audio is None


@pytest.mark.parametrize(
    ("model", "priced_as"),
    [
        ("gpt-6-luna", "gpt-6-luna"),
        ("gpt-6-luna-2026-09-01", "gpt-6-luna"),
        ("gpt-realtime-2.1-mini", "gpt-realtime-2.1-mini"),
        # The longest name it belongs to, not the first.
        ("gpt-realtime-2.1-mini-2026-08-15", "gpt-realtime-2.1-mini"),
        ("gpt-realtime-2.1-2026-08-15", "gpt-realtime-2.1"),
    ],
)
def test_a_dated_model_name_is_priced_as_its_model(model: str, priced_as: str) -> None:
    assert PRICES.price_for(model) == PRICES.models[priced_as]


@pytest.mark.parametrize("model", ["gemini-3-flash", "gpt-6-lunar", "", None])
def test_a_model_not_listed_has_no_price(model: str | None) -> None:
    assert PRICES.price_for(model) is None


def test_a_text_call_is_priced_with_its_cached_part_cheaper() -> None:
    # 5,820 uncached at 0.10, 20,000 cached at 0.01, 214 out at 0.50 per
    # million: 582 + 200 + 107 micro-dollars.
    call = {
        "model": "gpt-6-luna",
        "input_tokens": 25_820,
        "cached_input_tokens": 20_000,
        "output_tokens": 214,
    }
    assert call_cost(call, PRICES) == Cost(micro_usd=889, calls=1)


def test_a_call_logged_before_caching_was_recorded_counts_all_input_as_new() -> None:
    call = {"model": "gpt-6-luna", "input_tokens": 1000, "output_tokens": 100}
    assert call_cost(call, PRICES).micro_usd == 150


def test_a_voice_call_prices_audio_and_text_apart() -> None:
    call = {
        "model": "gpt-realtime-2.1-mini",
        "input_tokens": 1500,
        "output_tokens": 80,
        "input_text_tokens": 300,
        "input_audio_tokens": 1200,
        "cached_text_tokens": 200,
        "cached_audio_tokens": 800,
        "output_text_tokens": 20,
        "output_audio_tokens": 60,
    }
    # Text in: 100 x 0.60 + 200 x 0.06; audio in: 400 x 10 + 800 x 0.30;
    # out: 20 x 2.40 + 60 x 20.
    assert call_cost(call, PRICES) == Cost(
        micro_usd=60 + 12 + 4000 + 240 + 48 + 1200, calls=1
    )


def test_a_voice_call_without_the_split_is_priced_as_text_and_marked() -> None:
    call = {
        "model": "gpt-realtime-2.1-mini",
        "input_tokens": 1000,
        "output_tokens": 100,
    }
    assert call_cost(call, PRICES) == Cost(micro_usd=840, calls=1, estimated_calls=1)


@pytest.mark.parametrize(
    "call",
    [
        {"model": "gemini-3-flash", "input_tokens": 1000, "output_tokens": 10},
        {"model": "gpt-6-luna", "input_tokens": None, "output_tokens": None},
        {"model": None, "input_tokens": 10, "output_tokens": 10},
    ],
)
def test_an_unpriced_call_is_counted_but_costs_nothing(call: dict) -> None:
    assert call_cost(call, PRICES) == Cost(calls=1, unpriced_calls=1)


def test_costs_add_up() -> None:
    calls = [
        {"model": "gpt-6-luna", "input_tokens": 1000, "output_tokens": 100},
        {"model": "gpt-6-luna", "input_tokens": 1000, "output_tokens": 100},
        {"model": "unknown", "input_tokens": 1000, "output_tokens": 100},
    ]
    assert cost_of(calls, PRICES) == Cost(micro_usd=300, calls=3, unpriced_calls=1)
    assert cost_of([], PRICES) == Cost()


def test_the_shipped_price_list_covers_the_default_models() -> None:
    settings = Settings(database_url="postgresql+psycopg://unused", _env_file=None)  # pyright: ignore[reportCallIssue]
    prices = load_prices()

    text = prices.price_for(settings.openai_text_model)
    voice = prices.price_for(settings.voice_model)

    assert text is not None
    assert voice is not None
    assert voice.audio is not None
