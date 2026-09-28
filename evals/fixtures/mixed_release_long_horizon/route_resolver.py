from route_catalog import bucket_for, canonical_for
from route_models import Route


def resolve_route(target: str) -> Route:
    canonical = canonical_for(target)
    return Route(target_id=canonical, bucket=bucket_for(canonical))
