from dataclasses import dataclass


@dataclass(frozen=True)
class FlagContext:
    tenant_id: str
    user_id: str
