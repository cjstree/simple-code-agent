from flag_models import FlagContext


class FlagStore:
    def __init__(self) -> None:
        self._values: dict[str, bool] = {}

    def set_enabled(self, context: FlagContext, enabled: bool) -> None:
        self._values[context.user_id] = enabled

    def is_enabled(self, context: FlagContext) -> bool:
        return self._values.get(context.user_id, False)
