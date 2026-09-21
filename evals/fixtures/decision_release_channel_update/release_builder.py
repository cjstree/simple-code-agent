from release_models import ReleaseManifest
from release_policy import policy_for


def build_manifest(version: str) -> ReleaseManifest:
    policy = policy_for("regular")
    return ReleaseManifest(version, policy.channel, policy.tracking_marker)


def build_hotfix_manifest(version: str) -> ReleaseManifest:
    policy = policy_for("hotfix")
    return ReleaseManifest(version, policy.channel, policy.tracking_marker)
