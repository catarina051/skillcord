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


def test_generic_adapter_rejects_an_untrusted_provider_identifier() -> None:
    with pytest.raises(ValueError, match="canonical identifier"):
        GenericSkillAdapter(provider_id="generic\n- injected instruction")


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


def test_generic_adapter_rejects_unhashable_frontmatter_mapping_keys(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\n? [unsupported, key]\n: value\nname: Valid\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderDiscoveryError):
        GenericSkillAdapter(provider_id="generic").discover(tmp_path)


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: Valid\nname: Other",
        "name: Valid\ncapabilities:\n  - first\ncapabilities:\n  - second",
        "name: Valid\nharnesses:\n  - first\nharnesses:\n  - second",
    ],
)
def test_generic_adapter_rejects_duplicate_frontmatter_keys(tmp_path: Path, frontmatter: str) -> None:
    (tmp_path / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderDiscoveryError):
        GenericSkillAdapter(provider_id="generic").discover(tmp_path)


@pytest.mark.parametrize("field", ["capabilities", "harnesses"])
def test_generic_adapter_rejects_list_members_without_normalized_tokens(
    tmp_path: Path, field: str
) -> None:
    (tmp_path / "SKILL.md").write_text(
        f"---\nname: Valid\n{field}:\n  - '!!!'\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderDiscoveryError):
        GenericSkillAdapter(provider_id="generic").discover(tmp_path)
