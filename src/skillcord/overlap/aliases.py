"""Explicit, local capability alias normalization."""

from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from skillcord.normalization.ids import normalize_token


class AliasRegistry:
    """Resolve only aliases explicitly declared in the local registry."""

    def __init__(self, canonical_by_token: Mapping[str, str]) -> None:
        self._canonical_by_token = dict(canonical_by_token)

    @classmethod
    def from_path(cls, path: Path) -> "AliasRegistry":
        """Load a version-one alias registry from a YAML file."""

        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_document, dict):
            raise TypeError("alias registry must be a mapping")
        if raw_document.get("schema_version") != 1:
            raise ValueError("alias registry schema_version must be 1")

        raw_aliases = raw_document.get("aliases")
        if not isinstance(raw_aliases, dict):
            raise TypeError("alias registry aliases must be a mapping")

        canonical_by_token: dict[str, str] = {}
        for canonical, aliases in raw_aliases.items():
            if not isinstance(canonical, str):
                raise TypeError("alias registry capability IDs must be strings")
            canonical_id = normalize_token(canonical)
            if not canonical_id:
                raise ValueError("alias registry capability IDs must not be empty")
            if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                raise ValueError("alias registry aliases must be lists of strings")

            for token in [canonical, *aliases]:
                normalized_token = normalize_token(token)
                if not normalized_token:
                    raise ValueError("alias registry aliases must not be empty")
                previous = canonical_by_token.setdefault(normalized_token, canonical_id)
                if previous != canonical_id:
                    raise ValueError(f"alias {normalized_token!r} maps to multiple capabilities")

        return cls(canonical_by_token)

    @classmethod
    def load(cls, path: Path) -> "AliasRegistry":
        """Load a registry; retained as the public descriptive alias for ``from_path``."""

        return cls.from_path(path)

    def canonicalize(self, value: str) -> str:
        """Return an explicit canonical ID or the normalized unregistered token."""

        normalized_value = normalize_token(value)
        return self._canonical_by_token.get(normalized_value, normalized_value)
