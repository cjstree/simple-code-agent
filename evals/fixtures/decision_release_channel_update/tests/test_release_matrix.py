from release_matrix import channels_by_source, manifest_views


def test_manifest_matrix_keeps_sources_separate() -> None:
    views = manifest_views("3.0.0")
    assert [(view.source, view.kind) for view in views] == [
        ("current", "regular"),
        ("current", "hotfix"),
        ("draft", "preview"),
        ("archive", "regular"),
        ("archive", "hotfix"),
    ]
    assert all(view.manifest.version == "3.0.0" for view in views)
    assert channels_by_source("3.0.0")["archive"] == {
        "regular": "legacy",
        "hotfix": "legacy",
    }
