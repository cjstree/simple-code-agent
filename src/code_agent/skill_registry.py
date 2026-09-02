"""Discovery and prompt-facing metadata for local agent skills."""

from pathlib import Path
from typing import Any

import yaml


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse optional YAML frontmatter from a skill manifest."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        metadata = {}
    return metadata, parts[2].strip()


class SkillRegistry:
    """Store discovered skill manifests and render their prompt summary."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, str]] = {}

    @property
    def entries(self) -> dict[str, dict[str, str]]:
        return self._entries

    def scan(self, skills_dir: Path | None = None) -> None:
        """Replace entries with manifests found under immediate child folders."""
        self._entries = {}
        if skills_dir is None or not skills_dir.exists():
            return

        for directory in sorted(skills_dir.iterdir()):
            if not directory.is_dir():
                continue
            manifest = directory / "SKILL.md"
            if not manifest.exists():
                continue

            raw = manifest.read_text()
            metadata, _body = parse_frontmatter(raw)
            name = metadata.get("name", directory.name)
            description = metadata.get(
                "description",
                raw.split("\n")[0].lstrip("#").strip(),
            )
            self._entries[name] = {
                "name": name,
                "description": description,
                "content": raw,
            }

    def get_content(self, name: str) -> str | None:
        """Return a registered skill manifest, or None when it is unknown."""
        skill = self._entries.get(name)
        return skill["content"] if skill is not None else None

    def format_list(self) -> str:
        """Render the compact skill list embedded in the system prompt."""
        return "\n".join(
            f"- **{skill['name']}**: {skill['description']}"
            for skill in self._entries.values()
        )
