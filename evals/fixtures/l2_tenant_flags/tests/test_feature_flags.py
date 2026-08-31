from flag_service import FeatureFlagService
from flag_store import FlagStore


def test_flag_does_not_leak_to_same_user_id_in_another_tenant() -> None:
    service = FeatureFlagService(FlagStore())
    service.enable_for("tenant-a", "alex")

    assert service.enabled_for("tenant-b", "alex") is False


def test_enabled_flag_is_returned_in_its_own_tenant() -> None:
    service = FeatureFlagService(FlagStore())
    service.enable_for("tenant-a", "alex")

    assert service.enabled_for("tenant-a", "alex") is True
