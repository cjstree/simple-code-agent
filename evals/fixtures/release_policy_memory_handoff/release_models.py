from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    channel: str
    tracking_marker: str
