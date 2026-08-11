from pathlib import Path

from skillcord.adapters.base import AdapterContext
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.adapters.codex import CodexAdapter
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.provider import SkillRecord


def _context(tmp_path: Path) -> AdapterContext:
    skill = SkillRecord(
        provider_id="superpowers",
        skill_id="brainstorming",
        name="Brainstorming",
        source_path=tmp_path / "SKILL.md",
        content_hash="a" * 64,
        capabilities={"planning"},
        harnesses={"claude", "codex"},
    )
    project = ProjectConfig(
        schema_version=1,
        project=ProjectInfo(intent="api"),
        ai=AIConfig(harnesses=["claude", "codex"], desired_capabilities=["planning"]),
    )
    return AdapterContext(
        project=project,
        active_skills=(skill,),
        decisions=OverrideConfig(schema_version=1),
    )


def test_claude_adapter_adds_only_a_minimal_claude_specific_reference(
    tmp_path: Path,
) -> None:
    artifacts = ClaudeAdapter().plan(_context(tmp_path))

    assert [artifact.path for artifact in artifacts] == [Path("AGENTS.md"), Path("CLAUDE.md")]
    claude_artifact = artifacts[1]
    assert claude_artifact.ownership == "managed_block"
    assert claude_artifact.source_capability_ids == ("superpowers.brainstorming",)
    assert b"Claude Code" in claude_artifact.content
    assert b"AGENTS.md" in claude_artifact.content
    assert b"superpowers.brainstorming" not in claude_artifact.content
    assert b"Codex" not in claude_artifact.content


def test_claude_and_codex_share_identical_universal_policy(tmp_path: Path) -> None:
    context = _context(tmp_path)

    claude_agents = ClaudeAdapter().plan(context)[0]
    codex_agents = CodexAdapter().plan(context)[0]

    assert claude_agents.path == codex_agents.path == Path("AGENTS.md")
    assert claude_agents.content == codex_agents.content
    assert claude_agents.source_capability_ids == codex_agents.source_capability_ids
