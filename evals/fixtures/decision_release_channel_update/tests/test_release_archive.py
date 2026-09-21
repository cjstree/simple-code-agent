import pytest

from release_archive import build_archived_manifest


def test_archived_manifests_keep_their_recorded_policy() -> None:
    regular = build_archived_manifest("regular", "1.8.0")
    hotfix = build_archived_manifest("hotfix", "1.8.1")

    assert (regular.version, regular.channel, regular.tracking_marker) == (
        "1.8.0", "legacy", ""
    )
    assert (hotfix.version, hotfix.channel, hotfix.tracking_marker) == (
        "1.8.1", "legacy", ""
    )


def test_archive_rejects_unrecorded_release_kinds() -> None:
    with pytest.raises(KeyError):
        build_archived_manifest("preview", "1.8.0-preview")
