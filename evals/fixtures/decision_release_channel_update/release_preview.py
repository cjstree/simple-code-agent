from release_models import ReleaseManifest
from release_policy import policy_for


def build_preview_manifest(version: str) -> ReleaseManifest:
    """Build a draft manifest that is not published as a release."""
    policy = policy_for("preview")
    return ReleaseManifest(version, policy.channel, policy.tracking_marker)
