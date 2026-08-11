"""Tests for the root-only generic SKILL.md adapter."""

from pathlib import Path

import pytest

from skillcord.providers.base import ProviderDiscoveryError
from skillcord.providers.generic import GenericSkillAdapter


def test_generic_adapter_parses_single_skill_file() -> None:
    root = Path("tests/fixtures/providers/generic")
    snapshot = GenericSkillAdapter(provider_id="generic").discover(root)
    assert [s.normalized_id for s in snapshot.skills] == ["generic.test-driven-development"]
    assert snapshot.skills[0].capabilities == {"tdd"}


def test_generic_adapter_accepts_an_explicit_skill_file() -> None:
    skill_path = Path("tests/fixtures/providers/generic/SKILL.md")
    snapshot = GenericSkillAdapter(provider_id="generic").discover(skill_path)
    assert snapshot.root_path == skill_path.parent


def test_generic_adapter_does_not_search_nested_directories(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text("---\nname: Nested\n---\n", encoding="utf-8")

    with pytest.raises(ProviderDiscoveryError):
        GenericSkillAdapter(provider_id="generic").discover(tmp_path)


def test_generic_adapter_rejects_malformed_frontmatter(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("---\nname: [\n---\n", encoding="utf-8")

    with pytest.raises(ProviderDiscoveryError):
        GenericSkillAdapter(provider_id="generic").discover(tmp_path)
