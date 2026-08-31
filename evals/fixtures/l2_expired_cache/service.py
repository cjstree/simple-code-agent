from cache import Cache


class ProfileService:
    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def cached_profile(self, user_id: str) -> str | None:
        return self._cache.get(f"profile:{user_id}")
