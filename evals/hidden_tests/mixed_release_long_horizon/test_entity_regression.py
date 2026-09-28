from route_resolver import resolve_route


def test_eu_binding_exact() -> None:
    canonical = resolve_route("invoice-api-eu")
    alias = resolve_route("line-b")
    assert canonical == alias
    assert canonical.target_id == "invoice-api-eu"
    assert canonical.bucket == "violet-17"


def test_us_binding_exact() -> None:
    canonical = resolve_route("invoice-api-us")
    alias = resolve_route("line-a")
    assert canonical == alias
    assert canonical.target_id == "invoice-api-us"
    assert canonical.bucket == "amber-42"


def test_us_canonical_name_keeps_identity() -> None:
    assert resolve_route("invoice-api-us").target_id == "invoice-api-us"


def test_existing_apac_route_keeps_its_binding() -> None:
    canonical = resolve_route("invoice-api-apac")
    alias = resolve_route("line-c")
    assert canonical == alias
    assert canonical.target_id == "invoice-api-apac"
    assert canonical.bucket == "archive-04"
