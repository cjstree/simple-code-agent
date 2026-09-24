from release_policy import build_manifest


def test_published_manifest_uses_snapshot_tag() -> None:
    # The final published value must match the earlier tool result exactly.
    assert build_manifest("3.2.0").build_tag == "plume-0093e087"
