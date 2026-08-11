"""A strict, root-only adapter for standalone ``SKILL.md`` files."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from skillcord.locking.hashing import sha256_file
from skillcord.models.provider import ProviderComponent, ProviderSnapshot, SkillRecord
from skillcord.normalization.ids import normalize_token
from skillcord.providers.base import ProviderDiscoveryError


class _NoDuplicateKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects ambiguous mapping keys."""


def _construct_mapping_without_duplicates(
    loader: _NoDuplicateKeySafeLoader, node: Any, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unsupported unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key!r})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_without_duplicates,
)


class GenericSkillAdapter:
    """Discover one explicitly located generic ``SKILL.md`` file."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    def supports(self, root: Path) -> bool:
        """Return whether ``root`` is a SKILL.md file or contains one at its root."""

        return (root.is_file() and root.name == "SKILL.md") or (root.is_dir() and (root / "SKILL.md").is_file())

    def discover(self, root: Path) -> ProviderSnapshot:
        """Parse one root-level generic skill without traversing source directories."""

        skill_path = self._skill_path(root)
        metadata = self._read_metadata(skill_path)
        name = self._required_string(metadata, "name", skill_path)
        skill_id = normalize_token(name)
        if not skill_id:
            raise ProviderDiscoveryError(self.provider_id, skill_path, "frontmatter name normalizes to empty")

        source_root = skill_path.parent
        description = self._optional_string(metadata, "description", skill_path)
        return ProviderSnapshot(
            provider_id=self.provider_id,
            root_path=source_root,
            skills=[
                SkillRecord(
                    provider_id=self.provider_id,
                    skill_id=skill_id,
                    name=name,
                    description=description,
                    source_path=skill_path,
                    content_hash=sha256_file(skill_path),
                    capabilities=self._token_set(metadata, "capabilities", skill_path),
                    harnesses=self._token_set(metadata, "harnesses", skill_path),
                )
            ],
            components=[ProviderComponent(component_id=self.provider_id, source_path=source_root)],
        )

    def _skill_path(self, root: Path) -> Path:
        if root.is_file():
            if root.name == "SKILL.md":
                return root
        elif root.is_dir():
            skill_path = root / "SKILL.md"
            if skill_path.is_file():
                return skill_path
        raise ProviderDiscoveryError(
            self.provider_id,
            root,
            "expected an explicit SKILL.md file or a directory containing root SKILL.md",
        )

    def _read_metadata(self, skill_path: Path) -> Mapping[str, Any]:
        try:
            text = skill_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ProviderDiscoveryError(self.provider_id, skill_path, "SKILL.md is not UTF-8 text") from error

        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise ProviderDiscoveryError(self.provider_id, skill_path, "missing bounded YAML frontmatter")

        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
            None,
        )
        if closing_index is None:
            raise ProviderDiscoveryError(self.provider_id, skill_path, "unterminated YAML frontmatter")

        try:
            document = yaml.load("".join(lines[1:closing_index]), Loader=_NoDuplicateKeySafeLoader)
        except yaml.YAMLError as error:
            raise ProviderDiscoveryError(self.provider_id, skill_path, "malformed YAML frontmatter") from error
        if not isinstance(document, Mapping):
            raise ProviderDiscoveryError(self.provider_id, skill_path, "frontmatter must be a mapping")
        return document

    def _required_string(self, metadata: Mapping[str, Any], key: str, path: Path) -> str:
        value = self._optional_string(metadata, key, path)
        if value is None or not value.strip():
            raise ProviderDiscoveryError(self.provider_id, path, f"frontmatter {key!r} must be a non-empty string")
        return value

    def _optional_string(self, metadata: Mapping[str, Any], key: str, path: Path) -> str | None:
        value = metadata.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProviderDiscoveryError(self.provider_id, path, f"frontmatter {key!r} must be a string")
        return value

    def _token_set(self, metadata: Mapping[str, Any], key: str, path: Path) -> set[str]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ProviderDiscoveryError(self.provider_id, path, f"frontmatter {key!r} must be a list of strings")
        tokens: set[str] = set()
        for item in value:
            token = normalize_token(item)
            if not token:
                raise ProviderDiscoveryError(
                    self.provider_id,
                    path,
                    f"frontmatter {key!r} contains a value that normalizes to empty",
                )
            tokens.add(token)
        return tokens
