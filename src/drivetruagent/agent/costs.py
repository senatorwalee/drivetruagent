"""What the model calls cost, from the tokens the event log recorded. Pure logic.

Prices come from data/model_prices.json, in US dollars per million tokens,
with the date they were checked. A model that is not listed is counted as
unpriced rather than guessed at, so a total never claims more than it knows.

Amounts are whole micro-dollars (a millionth of a dollar). Conveniently,
tokens times a price per million tokens is exactly that, so nothing is
rounded until the very end.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_PRICES_PATH = Path("data/model_prices.json")


@dataclass(frozen=True)
class Rates:
    """US dollars per million tokens."""

    input: Decimal
    cached_input: Decimal
    output: Decimal


@dataclass(frozen=True)
class ModelPrice:
    text: Rates
    # Realtime models price audio apart from text. None: text only.
    audio: Rates | None = None


@dataclass(frozen=True)
class PriceList:
    checked_on: date
    source: str
    models: Mapping[str, ModelPrice]

    def price_for(self, model: str | None) -> ModelPrice | None:
        """The price of a model by its name, or of the model a dated name
        belongs to ("gpt-6-luna-2026-09-01" is priced as "gpt-6-luna")."""
        if not model:
            return None
        if model in self.models:
            return self.models[model]
        family = [name for name in self.models if model.startswith(f"{name}-")]
        return self.models[max(family, key=len)] if family else None


def load_prices(path: Path = DEFAULT_PRICES_PATH) -> PriceList:
    return parse_prices(json.loads(path.read_text()))


@lru_cache
def prices_at(path: Path) -> PriceList | None:
    """The price list at `path`, read once. None, with a warning, if it is
    missing or unreadable: costs are then left out, nothing else breaks."""
    try:
        return load_prices(path)
    except (OSError, ValueError, KeyError):
        logger.warning("No model prices at %s; costs will not be shown", path)
        return None


def parse_prices(data: Mapping[str, Any]) -> PriceList:
    def rates(values: Mapping[str, str]) -> Rates:
        return Rates(
            Decimal(values["input"]),
            Decimal(values["cached_input"]),
            Decimal(values["output"]),
        )

    return PriceList(
        checked_on=date.fromisoformat(data["checked_on"]),
        source=str(data["source"]),
        models={
            name: ModelPrice(
                rates(price["text"]),
                rates(price["audio"]) if "audio" in price else None,
            )
            for name, price in data["models"].items()
        },
    )


class Cost(BaseModel):
    """What a set of model calls cost, and how sure that figure is."""

    micro_usd: int = 0
    # Every call counted, priced or not.
    calls: int = 0
    # Calls whose model has no price, or which recorded no tokens: not in
    # micro_usd at all.
    unpriced_calls: int = 0
    # Voice calls logged before audio and text were told apart, priced as
    # if every token were text: low.
    estimated_calls: int = 0

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            micro_usd=self.micro_usd + other.micro_usd,
            calls=self.calls + other.calls,
            unpriced_calls=self.unpriced_calls + other.unpriced_calls,
            estimated_calls=self.estimated_calls + other.estimated_calls,
        )


def cost_of(calls: Iterable[Mapping[str, Any]], prices: PriceList) -> Cost:
    """The cost of model calls, each given as a model_call event's payload."""
    total = Cost()
    for call in calls:
        total += call_cost(call, prices)
    return total


def call_cost(call: Mapping[str, Any], prices: PriceList) -> Cost:
    price = prices.price_for(call.get("model"))
    tokens_in = _count(call.get("input_tokens"))
    tokens_out = _count(call.get("output_tokens"))
    if price is None or (tokens_in is None and tokens_out is None):
        return Cost(calls=1, unpriced_calls=1)
    if price.audio is not None and call.get("input_audio_tokens") is not None:
        micro = _realtime(call, price.text, price.audio)
        return Cost(micro_usd=micro, calls=1)
    cached = _count(call.get("cached_input_tokens")) or 0
    rates = price.text
    micro = (
        ((tokens_in or 0) - cached) * rates.input
        + cached * rates.cached_input
        + (tokens_out or 0) * rates.output
    )
    # A voice call from before the split was logged: priced as all text.
    estimated = price.audio is not None
    return Cost(micro_usd=round(micro), calls=1, estimated_calls=int(estimated))


def _realtime(call: Mapping[str, Any], text: Rates, audio: Rates) -> int:
    def tokens(key: str) -> int:
        return _count(call.get(key)) or 0

    cached_text = tokens("cached_text_tokens")
    cached_audio = tokens("cached_audio_tokens")
    micro = (
        (tokens("input_text_tokens") - cached_text) * text.input
        + cached_text * text.cached_input
        + (tokens("input_audio_tokens") - cached_audio) * audio.input
        + cached_audio * audio.cached_input
        + tokens("output_text_tokens") * text.output
        + tokens("output_audio_tokens") * audio.output
    )
    return round(micro)


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
