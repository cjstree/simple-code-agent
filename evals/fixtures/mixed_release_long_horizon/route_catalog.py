_BUCKETS = {
    "invoice-api-eu": "legacy",
    "invoice-api-us": "legacy",
    "invoice-api-apac": "archive-04",
}
_ALIASES = {
    "line-c": "invoice-api-apac",
}
_KNOWN_ALIAS_NAMES = {"line-a", "line-b"}


def canonical_for(name: str) -> str:
    if name in _BUCKETS:
        return name
    if name in _ALIASES:
        return _ALIASES[name]
    if name in _KNOWN_ALIAS_NAMES:
        return name
    raise KeyError(name)


def bucket_for(canonical: str) -> str:
    return _BUCKETS.get(canonical, "legacy")


def display_names() -> tuple[list[str], list[str]]:
    """Return two independently sorted lists for the admin picker."""
    return sorted(_BUCKETS), sorted(_ALIASES.keys() | _KNOWN_ALIAS_NAMES)
