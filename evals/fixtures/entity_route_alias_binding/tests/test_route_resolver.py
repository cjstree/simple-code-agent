import pytest
from route_admin import list_route_choices
from route_resolver import resolve_route


def test_line_b_resolves_to_canonical_target() -> None:
    assert resolve_route("line-b").target_id in {"invoice-api-eu", "invoice-api-us"}


def test_line_a_resolves_to_canonical_target() -> None:
    assert resolve_route("line-a").target_id in {"invoice-api-eu", "invoice-api-us"}


def test_unknown_route_raises_key_error() -> None:
    with pytest.raises(KeyError):
        resolve_route("unknown")


def test_admin_choices_remain_sorted_for_display() -> None:
    choices = list_route_choices()
    assert choices["targets"] == sorted(choices["targets"])
    assert choices["aliases"] == ["line-a", "line-b", "line-c"]
