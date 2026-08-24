"""Shared contracts for deterministic harness artifact generation."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from skillcord.models.config import OverrideConfig, ProjectConfig
from skillcord.models.provider import SkillRecord

ArtifactOwnership = Literal["managed_block", "owned_file"]


@dataclass(frozen=True)
class GeneratedArtifact:
    """A planned, not-yet-written harness configuration artifact."""

    path: Path
    ownership: ArtifactOwnership
    content: bytes
    source_capability_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdapterContext:
    """Resolved project state consumed by every harness adapter."""

    project: ProjectConfig
    active_skills: Sequence[SkillRecord]
    decisions: OverrideConfig


class HarnessAdapter(Protocol):
    """Generate deterministic artifacts without mutating the project."""

    harness_id: str

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        """Return the artifacts required by this harness."""


class ArtifactCollisionError(ValueError):
    """Raised when harnesses plan incompatible content for the same path."""


def compose_artifacts(
    adapters: Iterable[HarnessAdapter],
    context: AdapterContext,
) -> list[GeneratedArtifact]:
    """Coalesce identical artifacts and reject divergent same-path plans."""

    artifacts_by_path: dict[Path, GeneratedArtifact] = {}
    for adapter in sorted(adapters, key=lambda candidate: candidate.harness_id):
        for artifact in adapter.plan(context):
            existing = artifacts_by_path.get(artifact.path)
            if existing is None:
                artifacts_by_path[artifact.path] = artifact
            elif existing != artifact:
                raise ArtifactCollisionError(
                    f"harness artifact collision for path {artifact.path.as_posix()!r}"
                )

    return [
        artifacts_by_path[path]
        for path in sorted(artifacts_by_path, key=lambda candidate: candidate.as_posix())
    ]


def active_capability_ids(context: AdapterContext) -> tuple[str, ...]:
    """Return sorted, unique identifiers for the resolved active skills."""

    return tuple(sorted({skill.normalized_id for skill in context.active_skills}))
