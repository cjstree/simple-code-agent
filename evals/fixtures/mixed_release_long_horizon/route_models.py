from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    target_id: str
    bucket: str
