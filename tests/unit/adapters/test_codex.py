from pathlib import Path

from skillcord.adapters.base import AdapterContext
from skillcord.adapters.codex import CodexAdapter
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.provider import SkillRecord


def test_codex_adapter_uses_only_the_universal_agents_surface(tmp_path: Path) -> None:
    skill = SkillRecord(
        provider_id="ecc",
        skill_id="security-review",
        name="Security Review",
        source_path=tmp_path / "SKILL.md",
        content_hash="b" * 64,
        capabilities={"security"},
        harnesses={"codex"},
    )
    context = AdapterContext(
        project=ProjectConfig(
            schema_version=1,
            project=ProjectInfo(intent="api"),
            ai=AIConfig(harnesses=["codex"], desired_capabilities=["security"]),
        ),
        active_skills=(skill,),
        decisions=OverrideConfig(schema_version=1),
    )

    artifacts = CodexAdapter().plan(context)

    assert len(artifacts) == 1
    assert artifacts[0].path == Path("AGENTS.md")
    assert artifacts[0].ownership == "managed_block"
    assert artifacts[0].source_capability_ids == ("ecc.security-review",)
    assert b"Claude" not in artifacts[0].content
    assert b"Codex" not in artifacts[0].content
