from cache import Cache
from service import ProfileService


def test_expired_profile_is_a_cache_miss() -> None:
    cache = Cache(clock=lambda: 20.0)
    cache.set("profile:alice", "stale", expires_at=10.0)

    assert ProfileService(cache).cached_profile("alice") is None


def test_live_profile_is_returned() -> None:
    cache = Cache(clock=lambda: 20.0)
    cache.set("profile:alice", "current", expires_at=30.0)

    assert ProfileService(cache).cached_profile("alice") == "current"
