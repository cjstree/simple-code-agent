"""Search the admin inventory without resolving route names."""

from route_catalog import display_names


def search_inventory(prefix: str) -> dict[str, list[str]]:
    """Filter target and alias choices independently for the admin picker."""
    targets, aliases = display_names()
    return {
        "targets": [name for name in targets if name.startswith(prefix)],
        "aliases": [name for name in aliases if name.startswith(prefix)],
    }
