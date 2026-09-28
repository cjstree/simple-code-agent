from manifest_models import Manifest

_BUILD_TAGS = {
    "published": "",
    "preview": "preview-only",
}


def build_manifest(version: str) -> Manifest:
    return Manifest(version, "stable", _BUILD_TAGS["published"])


def build_preview_manifest(version: str) -> Manifest:
    return Manifest(version, "draft", _BUILD_TAGS["preview"])
