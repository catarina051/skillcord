from pathlib import Path

from skillcord.adapters.base import AdapterContext
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.adapters.codex import CodexAdapter
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.provider import SkillRecord
from skillcord.sync.applier import SyncApplier
from skillcord.sync.planner import SyncContext, SyncPlanner


def _context(root: Path, active_skills: tuple[SkillRecord, ...]) -> SyncContext:
    return SyncContext(
        project_root=root,
        adapter_context=AdapterContext(
            project=ProjectConfig(
                schema_version=1,
                project=ProjectInfo(intent="docs"),
                ai=AIConfig(harnesses=["claude", "codex"]),
            ),
            active_skills=active_skills,
            decisions=OverrideConfig(schema_version=1),
        ),
        adapters=(ClaudeAdapter(), CodexAdapter()),
    )


def _skill(root: Path, provider_id: str, skill_id: str) -> SkillRecord:
    return SkillRecord(
        provider_id=provider_id,
        skill_id=skill_id,
        name=skill_id,
        source_path=root / "providers" / provider_id / skill_id / "SKILL.md",
        content_hash="a" * 64,
    )


def test_plan_preview_then_apply_preserves_user_bytes_and_selectively_removes_humanizer(
    tmp_path: Path,
) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"\xff user\r\nuser suffix without newline")
    humanizer = _skill(tmp_path, "humanizer", "humanizer")
    brainstorming = _skill(tmp_path, "superpowers", "brainstorming")

    enabled_plan = SyncPlanner().plan(_context(tmp_path, (humanizer, brainstorming)))
    assert agents.read_bytes() == b"\xff user\r\nuser suffix without newline"
    SyncApplier().apply(enabled_plan, approved=True)
    enabled = agents.read_bytes()
    assert enabled.startswith(b"\xff user\r\nuser suffix without newline\r\n")
    assert b"humanizer.humanizer" in enabled

    disabled_plan = SyncPlanner().plan(_context(tmp_path, (brainstorming,)))
    assert b"humanizer.humanizer" in agents.read_bytes()
    SyncApplier().apply(disabled_plan, approved=True)
    disabled = agents.read_bytes()

    assert disabled.startswith(b"\xff user\r\nuser suffix without newline\r\n")
    assert b"humanizer.humanizer" not in disabled
    assert b"superpowers.brainstorming" in disabled
    assert (tmp_path / "CLAUDE.md").read_bytes().count(b"skillcord:begin") == 1
