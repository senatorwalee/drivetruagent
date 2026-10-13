"""A small in-memory cache whose entries expire.

For values that are asked for often and change rarely. It lives in one
process: with several workers each has its own copy, and a change made
through one is seen by the others only when their entries expire. That is
what the time to live is for.
"""

import time
from collections.abc import Callable
from datetime import timedelta


class TtlCache[KeyT, ValueT]:
    def __init__(
        self, ttl: timedelta, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """`ttl` is how long an entry is served. Zero means nothing is kept.

        `clock` returns seconds and only ever goes forwards. Tests pass their
        own so that time can be moved by hand.
        """
        self._ttl = ttl.total_seconds()
        self._clock = clock
        self._entries: dict[KeyT, tuple[float, ValueT]] = {}

    def get(self, key: KeyT) -> ValueT | None:
        """The value stored under `key`, or None if there is none or it expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self._clock() - stored_at >= self._ttl:
            del self._entries[key]
            return None
        return value

    def set(self, key: KeyT, value: ValueT) -> None:
        now = self._clock()
        # Dropping what has expired here keeps the cache from growing without
        # limit when keys are never asked for again (yesterday's date).
        self._entries = {
            other: entry
            for other, entry in self._entries.items()
            if now - entry[0] < self._ttl
        }
        self._entries[key] = (now, value)

    def invalidate(self) -> None:
        """Forget everything. The next read of any key goes to the source."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
