"""Collect several manifest views for an internal comparison screen."""

from dataclasses import dataclass

from release_archive import build_archived_manifest
from release_builder import build_hotfix_manifest, build_manifest
from release_models import ReleaseManifest
from release_preview import build_preview_manifest


@dataclass(frozen=True)
class ManifestView:
    source: str
    kind: str
    manifest: ReleaseManifest


def manifest_views(version: str) -> list[ManifestView]:
    return [
        ManifestView("current", "regular", build_manifest(version)),
        ManifestView("current", "hotfix", build_hotfix_manifest(version)),
        ManifestView("draft", "preview", build_preview_manifest(version)),
        ManifestView("archive", "regular", build_archived_manifest("regular", version)),
        ManifestView("archive", "hotfix", build_archived_manifest("hotfix", version)),
    ]


def channels_by_source(version: str) -> dict[str, dict[str, str]]:
    channels: dict[str, dict[str, str]] = {}
    for view in manifest_views(version):
        channels.setdefault(view.source, {})[view.kind] = view.manifest.channel
    return channels


def views_with_marker(version: str) -> list[ManifestView]:
    return [view for view in manifest_views(version) if view.manifest.tracking_marker]
