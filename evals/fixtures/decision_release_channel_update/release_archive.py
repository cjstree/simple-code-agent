"""Read manifests from an old, frozen release ledger."""

from release_models import ReleaseManifest


_ARCHIVED_POLICIES = {
    "regular": ("legacy", ""),
    "hotfix": ("legacy", ""),
}


def build_archived_manifest(kind: str, version: str) -> ReleaseManifest:
    """Reconstruct a past manifest without consulting current release policy."""
    channel, marker = _ARCHIVED_POLICIES[kind]
    return ReleaseManifest(version, channel, marker)
