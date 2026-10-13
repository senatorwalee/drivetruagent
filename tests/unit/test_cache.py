"""The expiring cache, with time moved by hand."""

from datetime import timedelta

from drivetruagent.cache import TtlCache


class Clock:
    """Seconds that only move when a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def cache_with(clock: Clock, seconds: float = 60) -> TtlCache[str, list[str]]:
    return TtlCache(timedelta(seconds=seconds), clock=clock)


def test_a_stored_value_comes_back() -> None:
    cache = cache_with(Clock())

    cache.set("monday", ["shake"])

    assert cache.get("monday") == ["shake"]


def test_a_key_that_was_never_stored_is_a_miss() -> None:
    assert cache_with(Clock()).get("monday") is None


def test_keys_are_separate() -> None:
    cache = cache_with(Clock())

    cache.set("monday", ["shake"])

    assert cache.get("friday") is None


def test_a_value_is_served_until_its_time_is_up() -> None:
    clock = Clock()
    cache = cache_with(clock, seconds=60)
    cache.set("monday", ["shake"])

    clock.advance(59.9)
    assert cache.get("monday") == ["shake"]

    clock.advance(0.1)
    assert cache.get("monday") is None


def test_reading_does_not_extend_a_values_life() -> None:
    """Otherwise a busy key would never be refreshed from its source."""
    clock = Clock()
    cache = cache_with(clock, seconds=60)
    cache.set("monday", ["shake"])

    for _ in range(5):
        clock.advance(15)
        cache.get("monday")

    assert cache.get("monday") is None


def test_storing_again_replaces_the_value_and_restarts_its_life() -> None:
    clock = Clock()
    cache = cache_with(clock, seconds=60)
    cache.set("monday", ["shake"])

    clock.advance(50)
    cache.set("monday", ["burger"])
    clock.advance(50)

    assert cache.get("monday") == ["burger"]


def test_invalidate_forgets_everything_at_once() -> None:
    cache = cache_with(Clock())
    cache.set("monday", ["shake"])
    cache.set("friday", ["burger"])

    cache.invalidate()

    assert cache.get("monday") is None
    assert cache.get("friday") is None
    assert len(cache) == 0


def test_an_empty_value_is_still_a_hit() -> None:
    """ "No specials today" is an answer worth remembering too."""
    cache = cache_with(Clock())

    cache.set("monday", [])

    assert cache.get("monday") == []


def test_a_time_to_live_of_zero_keeps_nothing() -> None:
    cache = cache_with(Clock(), seconds=0)

    cache.set("monday", ["shake"])

    assert cache.get("monday") is None


def test_expired_entries_are_dropped_when_something_new_is_stored() -> None:
    """Yesterday's date is never asked for again; it must not stay forever."""
    clock = Clock()
    cache = cache_with(clock, seconds=60)
    cache.set("monday", ["shake"])
    clock.advance(30)
    cache.set("tuesday", ["burger"])

    clock.advance(40)
    cache.set("wednesday", ["pie"])

    assert len(cache) == 2
    assert cache.get("tuesday") == ["burger"]
