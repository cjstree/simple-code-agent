from release_builder import build_hotfix_manifest, build_manifest


def test_hotfix_uses_a_distinct_release_channel() -> None:
    regular = build_manifest("2.4.0")
    hotfix = build_hotfix_manifest("2.4.1")

    assert hotfix.channel != regular.channel


def test_release_manifest_preserves_version() -> None:
    manifest = build_manifest("2.4.0")

    assert manifest.version == "2.4.0"
