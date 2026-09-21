from dataclasses import dataclass


@dataclass(frozen=True)
class ReleasePolicy:
    channel: str
    tracking_marker: str


_POLICIES = {
    "regular": ReleasePolicy("legacy", ""),
    "hotfix": ReleasePolicy("legacy", ""),
    "preview": ReleasePolicy("pilot", ""),
}


def policy_for(kind: str) -> ReleasePolicy:
    return _POLICIES[kind]
