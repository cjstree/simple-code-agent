from release_models import ReleaseManifest

DEFAULT_CHANNEL = "legacy"
TRACKING_MARKER = ""


def build_manifest(version: str) -> ReleaseManifest:
    return ReleaseManifest(
        version=version,
        channel=DEFAULT_CHANNEL,
        tracking_marker=TRACKING_MARKER,
    )


def build_hotfix_manifest(version: str) -> ReleaseManifest:
    return build_manifest(version)
