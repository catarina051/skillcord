"""Tests for provider source adapter selection."""

from pathlib import Path

from skillcord.discovery.sources import AdapterRegistry
from skillcord.providers.generic import GenericSkillAdapter
from skillcord.providers.superpowers import SuperpowersAdapter


def test_registry_selects_explicit_adapter_for_known_provider(tmp_path: Path) -> None:
    """Removing a known registration must not silently select the generic parser."""

    (tmp_path / "skills").mkdir()

    adapter = AdapterRegistry.default().adapter_for("superpowers", tmp_path)

    assert isinstance(adapter, SuperpowersAdapter)


def test_registry_uses_generic_adapter_only_for_well_formed_explicit_unknown_root(
    tmp_path: Path,
) -> None:
    """An unknown source without a root skill must remain unsupported."""

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    (valid_root / "SKILL.md").write_text("---\nname: Extra\n---\n", encoding="utf-8")
    malformed_root = tmp_path / "malformed"
    malformed_root.mkdir()
    (malformed_root / "README.md").write_text("not a skill", encoding="utf-8")
    registry = AdapterRegistry.default()

    assert isinstance(registry.adapter_for("additional", valid_root), GenericSkillAdapter)
    assert registry.adapter_for("additional", malformed_root) is None
