"""Tests for the explicit deterministic capability alias registry."""

from pathlib import Path

from skillcord.overlap.aliases import AliasRegistry


def test_registry_canonicalizes_explicit_aliases() -> None:
    """Removing a registry alias must stop its alternate spelling resolving."""

    registry = AliasRegistry.from_path(Path("registry/aliases/capabilities.yaml"))

    assert registry.canonicalize("TDD") == "test-driven-development"
    assert registry.canonicalize("test first development") == "test-driven-development"
    assert registry.canonicalize("review code") == "code-review"


def test_registry_normalizes_unknown_values_without_inventing_an_alias() -> None:
    """An unregistered token remains its normalized spelling, not a guessed capability."""

    registry = AliasRegistry.from_path(Path("registry/aliases/capabilities.yaml"))

    assert registry.canonicalize("Architecture Review") == "architecture-review"
