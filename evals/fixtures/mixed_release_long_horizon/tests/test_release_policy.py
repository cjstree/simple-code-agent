from snapshot_policy import build_manifest, build_preview_manifest


def test_published_manifest_has_build_tag() -> None:
    # A published manifest must expose the snapshot-derived tag.
    assert build_manifest("3.2.0").build_tag


def test_published_manifest_keeps_version_and_channel() -> None:
    # Adding the tag must leave the existing published fields intact.
    manifest = build_manifest("3.2.0")
    assert manifest.version == "3.2.0"
    assert manifest.channel == "stable"


def test_preview_manifest_keeps_its_policy() -> None:
    # A draft preview uses its separate tag and channel.
    preview = build_preview_manifest("3.3.0-rc1")
    assert preview.version == "3.3.0-rc1"
    assert preview.channel == "draft"
    assert preview.build_tag == "preview-only"
