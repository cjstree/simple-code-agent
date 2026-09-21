from release_builder import build_hotfix_manifest, build_manifest


def test_hotfix_uses_revised_channel() -> None:
    assert build_hotfix_manifest("2.4.1").channel == "emergency"


def test_regular_decisions_remain_in_force() -> None:
    regular = build_manifest("2.4.0")
    hotfix = build_hotfix_manifest("2.4.1")
    assert regular.channel == "staged"
    assert regular.tracking_marker == "KITE-407"
    assert hotfix.tracking_marker == "KITE-407"
