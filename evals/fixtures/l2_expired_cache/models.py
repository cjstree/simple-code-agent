from dataclasses import dataclass


@dataclass(frozen=True)
class CacheEntry:
    value: str
    expires_at: float
