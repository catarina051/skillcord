"""Tests for read-only Superpowers skill discovery."""

from pathlib import Path

import pytest

from skillcord.providers.superpowers import SuperpowersAdapter


class _FakeRevisionResolver:
    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    def resolve(self, path: Path) -> str | None:
        return self._revision


@pytest.fixture
def fake_revision_resolver() -> type[_FakeRevisionResolver]:
    return _FakeRevisionResolver


def test_superpowers_discovers_only_skill_tree(
    fake_revision_resolver: type[_FakeRevisionResolver],
) -> None:
    adapter = SuperpowersAdapter(revision_resolver=fake_revision_resolver("abc123"))
    snapshot = adapter.discover(Path("tests/fixtures/providers/superpowers"))

    assert {skill.normalized_id for skill in snapshot.skills} == {
        "superpowers.brainstorming",
        "superpowers.test-driven-development",
    }
    assert snapshot.components[0].revision == "abc123"
    assert snapshot.components[0].updater_can_mutate is True
    assert snapshot.components[0].artifact_hashes == {
        skill.source_path: skill.content_hash for skill in snapshot.skills
    }


def test_superpowers_ignores_unrelated_markdown(
    tmp_path: Path, fake_revision_resolver: type[_FakeRevisionResolver]
) -> None:
    (tmp_path / "README.md").write_text("---\nname: Ignored\n---\n", encoding="utf-8")
    ignored_directory = tmp_path / "notes"
    ignored_directory.mkdir()
    (ignored_directory / "SKILL.md").write_text("---\nname: Also ignored\n---\n", encoding="utf-8")

    skill_directory = tmp_path / "skills" / "kept"
    skill_directory.mkdir(parents=True)
    (skill_directory / "SKILL.md").write_text("---\nname: Kept\n---\n", encoding="utf-8")

    snapshot = SuperpowersAdapter(revision_resolver=fake_revision_resolver(None)).discover(tmp_path)

    assert [skill.normalized_id for skill in snapshot.skills] == ["superpowers.kept"]
