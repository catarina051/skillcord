"""Read-only discovery for the harness-managed Humanizer skill."""

from pathlib import Path

from skillcord.models.provider import ProviderComponent, ProviderSnapshot
from skillcord.providers.generic import GenericSkillAdapter


class HumanizerAdapter:
    """Discover the root Humanizer artifact without exposing text execution."""

    provider_id = "humanizer"

    def __init__(self) -> None:
        self._skill_adapter = GenericSkillAdapter(provider_id=self.provider_id)

    def supports(self, root: Path) -> bool:
        """Return whether ``root`` explicitly identifies a root ``SKILL.md`` artifact."""

        return self._skill_adapter.supports(root)

    def discover(self, root: Path) -> ProviderSnapshot:
        """Parse the root artifact and mark it optional and harness-only."""

        snapshot = self._skill_adapter.discover(root)
        resolved_root = snapshot.root_path.resolve()
        skill = snapshot.skills[0].model_copy(
            update={
                "source_path": snapshot.skills[0].source_path.resolve(),
                "optional": True,
                "execution_mode": "harness_only",
            }
        )
        return ProviderSnapshot(
            provider_id=self.provider_id,
            root_path=resolved_root,
            skills=[skill],
            components=[
                ProviderComponent(
                    component_id=self.provider_id,
                    source_path=resolved_root,
                    artifact_hashes={skill.source_path: skill.content_hash},
                )
            ],
        )
