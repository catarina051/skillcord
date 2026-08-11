from dataclasses import replace
from pathlib import Path

import pytest

from skillcord.adapters.base import (
    AdapterContext,
    ArtifactCollisionError,
    GeneratedArtifact,
    compose_artifacts,
)
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.adapters.codex import CodexAdapter
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.models.provider import SkillRecord


def _context(tmp_path: Path) -> AdapterContext:
    return AdapterContext(
        project=ProjectConfig(
            schema_version=1,
            project=ProjectInfo(intent="api"),
            ai=AIConfig(harnesses=["claude", "codex"], desired_capabilities=["planning"]),
        ),
        active_skills=(
            SkillRecord(
                provider_id="superpowers",
                skill_id="brainstorming",
                name="Brainstorming",
                source_path=tmp_path / "SKILL.md",
                content_hash="a" * 64,
            ),
        ),
        decisions=OverrideConfig(schema_version=1),
    )


def test_claude_and_codex_composition_coalesces_the_shared_agents_artifact(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    claude_first = compose_artifacts((ClaudeAdapter(), CodexAdapter()), context)
    codex_first = compose_artifacts((CodexAdapter(), ClaudeAdapter()), context)

    assert claude_first == codex_first
    assert [artifact.path for artifact in claude_first] == [Path("AGENTS.md"), Path("CLAUDE.md")]


class _StaticAdapter:
    harness_id = "static"

    def __init__(self, artifact: GeneratedArtifact) -> None:
        self.artifact = artifact

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        return [self.artifact]


@pytest.mark.parametrize(
    "divergent",
    [
        GeneratedArtifact(
            path=Path("AGENTS.md"),
            ownership="owned_file",
            content=b"shared",
            source_capability_ids=("provider.skill",),
        ),
        GeneratedArtifact(
            path=Path("AGENTS.md"),
            ownership="managed_block",
            content=b"different",
            source_capability_ids=("provider.skill",),
        ),
        GeneratedArtifact(
            path=Path("AGENTS.md"),
            ownership="managed_block",
            content=b"shared",
            source_capability_ids=("other.skill",),
        ),
    ],
)
def test_composition_rejects_divergent_same_path_artifacts(
    tmp_path: Path,
    divergent: GeneratedArtifact,
) -> None:
    original = replace(
        divergent,
        ownership="managed_block",
        content=b"shared",
        source_capability_ids=("provider.skill",),
    )

    with pytest.raises(ArtifactCollisionError, match="AGENTS.md"):
        compose_artifacts(
            (_StaticAdapter(original), _StaticAdapter(divergent)),
            _context(tmp_path),
        )
