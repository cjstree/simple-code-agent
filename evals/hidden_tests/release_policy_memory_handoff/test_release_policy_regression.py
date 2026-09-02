from release_builder import build_hotfix_manifest, build_manifest


def test_regular_release_uses_remembered_default_channel() -> None:
    manifest = build_manifest("3.0.0")

    assert manifest.channel == "canary-v2"
    assert manifest.tracking_marker == "ORCHID-731"


def test_hotfix_overrides_only_the_release_channel() -> None:
    manifest = build_hotfix_manifest("3.0.1")

    assert manifest.version == "3.0.1"
    assert manifest.channel == "stable"
    assert manifest.tracking_marker == "ORCHID-731"
