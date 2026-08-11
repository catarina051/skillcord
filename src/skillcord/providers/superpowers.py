"""Read-only discovery for the current Superpowers skill layout."""

from pathlib import Path
from typing import Protocol

from skillcord.models.provider import ProviderComponent, ProviderSnapshot, SkillRecord
from skillcord.providers.base import ProviderDiscoveryError
from skillcord.providers.generic import GenericSkillAdapter


class RevisionResolver(Protocol):
    """Resolve an optional immutable revision for a provider component."""

    def resolve(self, path: Path) -> str | None:
        """Return an immutable revision for ``path`` when one is available."""


class SuperpowersAdapter:
    """Discover declarative Superpowers skills without executing provider code."""

    provider_id = "superpowers"

    def __init__(self, revision_resolver: RevisionResolver | None = None) -> None:
        self._revision_resolver = revision_resolver
        self._skill_adapter = GenericSkillAdapter(provider_id=self.provider_id)

    def supports(self, root: Path) -> bool:
        """Return whether ``root`` has the current Superpowers skills directory."""

        return root.is_dir() and (root / "skills").is_dir()

    def discover(self, root: Path) -> ProviderSnapshot:
        """Discover only direct ``skills/*/SKILL.md`` artifacts under ``root``."""

        if not self.supports(root):
            raise ProviderDiscoveryError(self.provider_id, root, "expected a directory containing skills")

        resolved_root = root.resolve()
        skills = [self._discover_skill(path) for path in self._skill_paths(root)]
        revision = self._resolve_revision(resolved_root)
        resolved_skills = [
            skill.model_copy(
                update={"source_path": skill.source_path.resolve(), "source_revision": revision}
            )
            for skill in skills
        ]
        return ProviderSnapshot(
            provider_id=self.provider_id,
            root_path=resolved_root,
            skills=resolved_skills,
            components=[
                ProviderComponent(
                    component_id=self.provider_id,
                    source_path=resolved_root,
                    source_revision=revision,
                    updater_can_mutate=True,
                    artifact_hashes={skill.source_path: skill.content_hash for skill in resolved_skills},
                )
            ],
        )

    def _skill_paths(self, root: Path) -> list[Path]:
        skills_root = root / "skills"
        return sorted(
            (
                child / "SKILL.md"
                for child in skills_root.iterdir()
                if child.is_dir() and (child / "SKILL.md").is_file()
            ),
            key=lambda path: path.as_posix(),
        )

    def _discover_skill(self, skill_path: Path) -> SkillRecord:
        return self._skill_adapter.discover(skill_path).skills[0]

    def _resolve_revision(self, root: Path) -> str | None:
        if self._revision_resolver is None:
            return None
        return self._revision_resolver.resolve(root)
