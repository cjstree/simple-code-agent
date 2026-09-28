from route_catalog import display_names


def list_route_choices() -> dict[str, list[str]]:
    targets, aliases = display_names()
    return {"targets": targets, "aliases": aliases}
