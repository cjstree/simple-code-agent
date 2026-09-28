"""Build rows for a catalog inspection screen."""

from dataclasses import dataclass

from route_admin import list_route_choices
from route_inventory import search_inventory
from route_resolver import resolve_route


@dataclass(frozen=True)
class RouteInspection:
    name: str
    name_type: str
    resolved_target: str
    bucket: str
    status: str


def inspect_catalog(prefix: str = "") -> list[RouteInspection]:
    known_targets = set(list_route_choices()["targets"])
    matches = search_inventory(prefix)
    rows = []
    for name_type in ("targets", "aliases"):
        for name in matches[name_type]:
            route = resolve_route(name)
            rows.append(
                RouteInspection(
                    name=name,
                    name_type=name_type,
                    resolved_target=route.target_id,
                    bucket=route.bucket,
                    status="bound" if route.target_id in known_targets else "unbound",
                )
            )
    return rows


def unresolved_names(prefix: str = "") -> list[str]:
    return [row.name for row in inspect_catalog(prefix) if row.status == "unbound"]
