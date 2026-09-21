from route_diagnostics import inspect_catalog


def test_catalog_inspection_keeps_display_names() -> None:
    rows = inspect_catalog("line-")
    assert [row.name for row in rows] == ["line-a", "line-b", "line-c"]
    assert all(row.name_type == "aliases" for row in rows)
    assert rows[-1].resolved_target == "invoice-api-apac"
