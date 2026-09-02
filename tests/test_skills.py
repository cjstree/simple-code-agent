import pytest

from code_agent.agent import Agent
from code_agent.skill_registry import SkillRegistry, parse_frontmatter
from code_agent.telemetry import AgentTelemetry
from code_agent.tool import LoadSkillsTool


def test_parse_frontmatter_returns_metadata_and_body() -> None:
    # Scenario: valid frontmatter is separated from the instruction body.
    raw = "---\nname: reviewing\ndescription: Review Python code\n---\n# Steps"

    metadata, body = parse_frontmatter(raw)

    assert metadata == {
        "name": "reviewing",
        "description": "Review Python code",
    }
    assert body == "# Steps"


def test_parse_frontmatter_leaves_plain_markdown_unchanged() -> None:
    # Scenario: a manifest without frontmatter remains plain Markdown.
    raw = "# Plain skill\n\nInstructions"

    metadata, body = parse_frontmatter(raw)

    assert metadata == {}
    assert body == raw


def test_parse_frontmatter_tolerates_invalid_yaml() -> None:
    # Scenario: malformed YAML is ignored while the skill body remains usable.
    raw = "---\nname: [unterminated\n---\nInstructions"

    metadata, body = parse_frontmatter(raw)

    assert metadata == {}
    assert body == "Instructions"


def test_skill_registry_scans_manifests_from_explicit_directory(tmp_path) -> None:
    # Scenario: scanning registers only immediate child SKILL.md manifests.
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    alpha_manifest = (
        "---\nname: custom-alpha\ndescription: Alpha description\n---\nAlpha body"
    )
    (alpha / "SKILL.md").write_text(alpha_manifest, encoding="utf-8")
    beta = tmp_path / "beta"
    beta.mkdir()
    beta_manifest = "# Beta description\n\nBeta body"
    (beta / "SKILL.md").write_text(beta_manifest, encoding="utf-8")
    (tmp_path / "not-a-skill.md").write_text("ignored", encoding="utf-8")

    registry = SkillRegistry()
    registry.scan(tmp_path)

    assert registry.entries == {
        "custom-alpha": {
            "name": "custom-alpha",
            "description": "Alpha description",
            "content": alpha_manifest,
        },
        "beta": {
            "name": "beta",
            "description": "Beta description",
            "content": beta_manifest,
        },
    }


def test_skill_registry_accepts_no_configured_directory() -> None:
    # Scenario: scanning without a configured directory clears the registry.
    registry = SkillRegistry()

    registry.scan(None)

    assert registry.entries == {}


def test_skill_registry_formats_entries_for_the_system_prompt(tmp_path) -> None:
    # Scenario: the prompt list preserves registry order and compact formatting.
    reviewing = tmp_path / "reviewing"
    reviewing.mkdir()
    (reviewing / "SKILL.md").write_text(
        "---\nname: reviewing\ndescription: Review Python code\n---\nReview",
        encoding="utf-8",
    )
    testing = tmp_path / "testing"
    testing.mkdir()
    (testing / "SKILL.md").write_text(
        "---\nname: testing\ndescription: Test Python code\n---\nTest",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    registry.scan(tmp_path)

    assert registry.format_list() == (
        "- **reviewing**: Review Python code\n- **testing**: Test Python code"
    )


def test_agent_owns_skill_registry(tmp_path) -> None:
    # Scenario: Agent exposes its SkillRegistry as the single skill source of truth.
    skill_dir = tmp_path / "reviewing"
    skill_dir.mkdir()
    manifest = (
        "---\nname: reviewing\ndescription: Review Python code\n---\nInstructions"
    )
    (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")
    agent = Agent(telemetry=AgentTelemetry())

    agent.skill_registry.scan(tmp_path)

    assert agent.skill_registry.get_content("reviewing") == manifest
    assert agent.skill_registry.format_list() == "- **reviewing**: Review Python code"


@pytest.mark.asyncio
async def test_load_skills_returns_registered_manifest(tmp_path) -> None:
    # Scenario: loading a registered skill returns its complete manifest.
    skill_dir = tmp_path / "reviewing"
    skill_dir.mkdir()
    manifest = "full skill instructions"
    (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")
    registry = SkillRegistry()
    registry.scan(tmp_path)
    tool = LoadSkillsTool(registry)

    result = await tool.run({"skill_name": "reviewing"})

    assert result == manifest


@pytest.mark.asyncio
async def test_load_skills_reports_unknown_skill() -> None:
    # Scenario: requesting an unknown skill returns a readable tool error.
    tool = LoadSkillsTool(SkillRegistry())

    result = await tool.run({"skill_name": "missing"})

    assert result == "error: skill name: missing not found!"
