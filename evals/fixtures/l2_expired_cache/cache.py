from collections.abc import Callable

from models import CacheEntry


class Cache:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._entries: dict[str, CacheEntry] = {}

    def set(self, key: str, value: str, *, expires_at: float) -> None:
        self._entries[key] = CacheEntry(value=value, expires_at=expires_at)

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at < self._clock():
            return entry.value
        return entry.value
