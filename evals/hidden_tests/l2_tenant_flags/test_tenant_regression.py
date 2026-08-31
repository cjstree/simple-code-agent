from flag_models import FlagContext
from flag_store import FlagStore


def test_disabling_one_tenant_does_not_change_another_tenant() -> None:
    store = FlagStore()
    first = FlagContext("north", "sam")
    second = FlagContext("south", "sam")
    store.set_enabled(first, True)
    store.set_enabled(second, False)

    assert store.is_enabled(first) is True
    assert store.is_enabled(second) is False


def test_users_remain_independent_within_one_tenant() -> None:
    store = FlagStore()
    store.set_enabled(FlagContext("north", "sam"), True)

    assert store.is_enabled(FlagContext("north", "sam")) is True
    assert store.is_enabled(FlagContext("north", "lee")) is False
