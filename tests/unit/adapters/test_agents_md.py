from pathlib import Path

from skillcord.adapters.agents_md import AgentsMdAdapter, render_agents_policy
from skillcord.adapters.base import AdapterContext
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.provider import SkillRecord


def _project() -> ProjectConfig:
    return ProjectConfig(
        schema_version=1,
        project=ProjectInfo(intent="api"),
        ai=AIConfig(harnesses=["claude", "codex"], desired_capabilities=["testing"]),
    )


def _skill(
    tmp_path: Path,
    provider_id: str,
    skill_id: str,
    prompt_body: str,
) -> SkillRecord:
    source_path = tmp_path / provider_id / skill_id / "SKILL.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(prompt_body, encoding="utf-8")
    return SkillRecord(
        provider_id=provider_id,
        skill_id=skill_id,
        name=skill_id,
        source_path=source_path,
        content_hash="a" * 64,
        capabilities={skill_id},
        harnesses={"claude", "codex"},
    )


def test_agents_policy_references_active_ids_without_copying_provider_bodies(
    tmp_path: Path,
) -> None:
    secret_prompt = "ARBITRARY PROVIDER PROMPT BODY MUST NOT BE COPIED"
    skills = (
        _skill(tmp_path, "superpowers", "test-driven-development", secret_prompt),
        _skill(tmp_path, "ecc", "security-review", "Another provider instruction body"),
    )
    context = AdapterContext(
        project=_project(),
        active_skills=skills,
        decisions=OverrideConfig(schema_version=1),
    )

    artifact = AgentsMdAdapter().plan(context)[0]

    assert artifact.path == Path("AGENTS.md")
    assert artifact.ownership == "managed_block"
    assert artifact.source_capability_ids == (
        "ecc.security-review",
        "superpowers.test-driven-development",
    )
    assert b"ecc.security-review" in artifact.content
    assert b"superpowers.test-driven-development" in artifact.content
    assert secret_prompt.encode() not in artifact.content
    assert b"Another provider instruction body" not in artifact.content


def test_agents_policy_excludes_inactive_ids(tmp_path: Path) -> None:
    active = _skill(tmp_path, "superpowers", "brainstorming", "active body")
    inactive = _skill(tmp_path, "ecc", "security-review", "inactive body")
    context = AdapterContext(
        project=_project(),
        active_skills=(active,),
        decisions=OverrideConfig(schema_version=1),
    )

    text = AgentsMdAdapter().plan(context)[0].content.decode("utf-8")

    assert active.normalized_id in text
    assert inactive.normalized_id not in text


def test_humanizer_policy_is_opt_in() -> None:
    text = render_agents_policy(active_ids={"humanizer.humanizer"})

    assert "explicitly requests prose humanization" in text
    assert "Do not automatically apply" in text


def test_humanizer_policy_is_absent_when_capability_is_inactive() -> None:
    text = render_agents_policy(active_ids={"superpowers.brainstorming"})

    assert "explicitly requests prose humanization" not in text
    assert "Do not automatically apply" not in text


def test_agents_policy_rendering_is_deterministic() -> None:
    first = render_agents_policy(active_ids={"superpowers.zeta", "ecc.alpha"})
    second = render_agents_policy(active_ids={"ecc.alpha", "superpowers.zeta"})

    assert first == second
    assert first.index("ecc.alpha") < first.index("superpowers.zeta")
