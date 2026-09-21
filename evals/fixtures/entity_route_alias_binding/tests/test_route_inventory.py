from route_inventory import search_inventory


def test_inventory_filters_targets_and_aliases_separately() -> None:
    assert search_inventory("invoice-api-") == {
        "targets": ["invoice-api-apac", "invoice-api-eu", "invoice-api-us"],
        "aliases": [],
    }
    assert search_inventory("line-") == {
        "targets": [],
        "aliases": ["line-a", "line-b", "line-c"],
    }
