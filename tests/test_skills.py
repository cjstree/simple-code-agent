import pytest

from code_agent.agent import Agent, _parse_frontmatter
from code_agent.telemetry import AgentTelemetry
from code_agent.tool import LoadSkillsTool


# Scenario: a valid SKILL.md frontmatter is separated from its instruction body.
def test_parse_frontmatter_returns_metadata_and_body() -> None:
    raw = "---\nname: reviewing\ndescription: Review Python code\n---\n# Steps"

    metadata, body = _parse_frontmatter(raw)

    assert metadata == {
        "name": "reviewing",
        "description": "Review Python code",
    }
    assert body == "# Steps"


# Scenario: a SKILL.md without frontmatter remains plain Markdown.
def test_parse_frontmatter_leaves_plain_markdown_unchanged() -> None:
    raw = "# Plain skill\n\nInstructions"

    metadata, body = _parse_frontmatter(raw)

    assert metadata == {}
    assert body == raw


# Scenario: scanning an explicit root registers only child SKILL.md manifests.
def test_scan_skills_registers_manifests_from_explicit_directory(tmp_path) -> None:
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

    agent = Agent(telemetry=AgentTelemetry())
    agent._scan_skills(tmp_path)

    assert agent.skill_registry == {
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


# Scenario: starting without a configured skills directory yields an empty registry.
def test_scan_skills_accepts_no_configured_directory() -> None:
    agent = Agent(telemetry=AgentTelemetry())

    agent._scan_skills(None)

    assert agent.skill_registry == {}


# Scenario: loading a registered skill returns its complete manifest content.
@pytest.mark.asyncio
async def test_load_skills_returns_registered_manifest() -> None:
    tool = LoadSkillsTool({"reviewing": {"content": "full skill instructions"}})

    result = await tool.run({"skill_name": "reviewing"})

    assert result == "full skill instructions"


# Scenario: requesting an unknown skill returns a readable tool error.
@pytest.mark.asyncio
async def test_load_skills_reports_unknown_skill() -> None:
    tool = LoadSkillsTool({})

    result = await tool.run({"skill_name": "missing"})

    assert result == "error: skill name: missing not found!"
