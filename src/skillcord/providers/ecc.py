"""Read-only discovery for the supported declarative ECC fixture layout."""

from pathlib import Path

from skillcord.locking.hashing import sha256_file
from skillcord.models.provider import (
    ProviderComponent,
    ProviderSnapshot,
    SkillRecord,
    UnsupportedAsset,
)
from skillcord.normalization.ids import normalize_token
from skillcord.providers.base import ProviderDiscoveryError
from skillcord.providers.generic import GenericSkillAdapter


class ECCAdapter:
    """Discover ECC's declarative assets and report hooks as inactive."""

    provider_id = "ecc"

    def __init__(self) -> None:
        self._skill_adapter = GenericSkillAdapter(provider_id=self.provider_id)

    def supports(self, root: Path) -> bool:
        """Return whether ``root`` has the supported ECC declarative skill tree."""

        return root.is_dir() and (root / "skills").is_dir()

    def discover(self, root: Path) -> ProviderSnapshot:
        """Discover declarative assets without executing provider hooks or commands."""

        if not self.supports(root):
            raise ProviderDiscoveryError(self.provider_id, root, "expected a directory containing skills")

        resolved_root = root.resolve()
        skills = [self._discover_skill(path) for path in self._skill_paths(root)]
        metadata_components = self._metadata_components(root)
        unsupported_assets = self._unsupported_hook_assets(root)
        return ProviderSnapshot(
            provider_id=self.provider_id,
            root_path=resolved_root,
            skills=[
                skill.model_copy(update={"source_path": skill.source_path.resolve()}) for skill in skills
            ],
            components=[
                ProviderComponent(component_id=self.provider_id, source_path=resolved_root),
                *metadata_components,
            ],
            partial_support=bool(unsupported_assets),
            unsupported_assets=unsupported_assets,
        )

    def _skill_paths(self, root: Path) -> list[Path]:
        skills_root = root / "skills"
        return sorted(
            (path for path in skills_root.glob("**/SKILL.md") if path.is_file()),
            key=lambda path: path.as_posix(),
        )

    def _discover_skill(self, skill_path: Path) -> SkillRecord:
        return self._skill_adapter.discover(skill_path).skills[0]

    def _metadata_components(self, root: Path) -> list[ProviderComponent]:
        components: list[ProviderComponent] = []
        for directory_name, kind in (("agents", "agent"), ("commands", "command")):
            directory = root / directory_name
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md"), key=lambda item: item.as_posix()):
                try:
                    metadata = self._skill_adapter._read_metadata(path)
                    name = self._skill_adapter._required_string(metadata, "name", path)
                    metadata_id = normalize_token(name)
                    if not metadata_id:
                        raise ProviderDiscoveryError(
                            self.provider_id,
                            path,
                            "frontmatter name normalizes to empty",
                        )
                except ProviderDiscoveryError:
                    continue
                resolved_path = path.resolve()
                components.append(
                    ProviderComponent(
                        component_id=f"{self.provider_id}.{kind}.{metadata_id}",
                        source_path=resolved_path,
                        artifact_hashes={resolved_path: sha256_file(resolved_path)},
                    )
                )
        return components

    def _unsupported_hook_assets(self, root: Path) -> list[UnsupportedAsset]:
        hooks_path = root / "hooks" / "hooks.json"
        if not hooks_path.is_file():
            return []
        return [
            UnsupportedAsset(
                kind="executable-hook",
                source_path=hooks_path.resolve(),
                reason="ECC executable hooks are unsupported and remain inactive in V1",
            )
        ]
