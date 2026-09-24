from dataclasses import dataclass


@dataclass(frozen=True)
class Manifest:
    version: str
    channel: str
    build_tag: str
