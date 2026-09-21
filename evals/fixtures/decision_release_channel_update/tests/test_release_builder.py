from release_builder import build_hotfix_manifest, build_manifest
from release_preview import build_preview_manifest


def test_hotfix_channel_differs_from_regular() -> None:
    assert build_hotfix_manifest("2.4.1").channel != build_manifest("2.4.0").channel


def test_regular_manifest_has_tracking_marker() -> None:
    assert build_manifest("2.4.0").tracking_marker


def test_hotfix_keeps_version() -> None:
    assert build_hotfix_manifest("2.4.1").version == "2.4.1"


def test_regular_keeps_version() -> None:
    assert build_manifest("2.4.0").version == "2.4.0"


def test_preview_keeps_its_draft_channel() -> None:
    preview = build_preview_manifest("2.5.0-preview")
    assert preview.channel == "pilot"
    assert preview.tracking_marker == ""
    assert preview.version == "2.5.0-preview"
