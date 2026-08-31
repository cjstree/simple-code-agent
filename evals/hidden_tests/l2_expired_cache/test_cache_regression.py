from cache import Cache


def test_entry_expiring_now_is_a_cache_miss() -> None:
    cache = Cache(clock=lambda: 50.0)
    cache.set("session", "old", expires_at=50.0)

    assert cache.get("session") is None


def test_missing_and_unrelated_live_entries_keep_their_behavior() -> None:
    cache = Cache(clock=lambda: 50.0)
    cache.set("live", "value", expires_at=51.0)

    assert cache.get("missing") is None
    assert cache.get("live") == "value"
