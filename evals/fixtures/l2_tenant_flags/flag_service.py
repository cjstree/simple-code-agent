from flag_models import FlagContext
from flag_store import FlagStore


class FeatureFlagService:
    def __init__(self, store: FlagStore) -> None:
        self._store = store

    def enable_for(self, tenant_id: str, user_id: str) -> None:
        self._store.set_enabled(FlagContext(tenant_id, user_id), True)

    def enabled_for(self, tenant_id: str, user_id: str) -> bool:
        return self._store.is_enabled(FlagContext(tenant_id, user_id))
