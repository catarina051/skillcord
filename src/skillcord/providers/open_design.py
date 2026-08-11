"""Read-only discovery for Open Design declarative skills."""

from pathlib import Path

from skillcord.models.provider import ProviderComponent, ProviderSnapshot, SkillRecord
from skillcord.providers.base import ProviderDiscoveryError
from skillcord.providers.generic import GenericSkillAdapter


class OpenDesignAdapter:
    """Discover known Open Design skill artifacts without managing its runtime."""

    provider_id = "open_design"

    def __init__(self) -> None:
        self._skill_adapter = GenericSkillAdapter(provider_id=self.provider_id)

    def supports(self, root: Path) -> bool:
        """Return whether ``root`` contains the known declarative skill directory."""

        return root.is_dir() and (root / "skills").is_dir()

    def discover(self, root: Path) -> ProviderSnapshot:
        """Discover direct ``skills/*/SKILL.md`` files without starting external runtimes."""

        if not self.supports(root):
            raise ProviderDiscoveryError(self.provider_id, root, "expected a directory containing skills")

        resolved_root = root.resolve()
        skills = [self._discover_skill(path) for path in self._skill_paths(root)]
        resolved_skills = [
            skill.model_copy(update={"source_path": skill.source_path.resolve()}) for skill in skills
        ]
        return ProviderSnapshot(
            provider_id=self.provider_id,
            root_path=resolved_root,
            skills=resolved_skills,
            components=[
                ProviderComponent(
                    component_id=self.provider_id,
                    source_path=resolved_root,
                    artifact_hashes={
                        skill.source_path: skill.content_hash for skill in resolved_skills
                    },
                )
            ],
            runtime_requirements={"mcp": "external", "daemon": "external"},
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
