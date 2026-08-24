"""Tests for declarative-only Open Design discovery."""

import subprocess
from pathlib import Path

from skillcord.providers.open_design import OpenDesignAdapter


def test_open_design_discovers_declarative_skills_only() -> None:
    snapshot = OpenDesignAdapter().discover(Path("tests/fixtures/providers/open_design"))

    assert [skill.normalized_id for skill in snapshot.skills] == ["open_design.ui-design"]
    assert snapshot.runtime_requirements == {"mcp": "external", "daemon": "external"}


def test_open_design_ignores_unknown_skill_locations(tmp_path: Path) -> None:
    known_skill = tmp_path / "skills" / "kept" / "SKILL.md"
    known_skill.parent.mkdir(parents=True)
    known_skill.write_text("---\nname: Kept\n---\n", encoding="utf-8")
    unknown_skill = tmp_path / "other" / "SKILL.md"
    unknown_skill.parent.mkdir()
    unknown_skill.write_text("---\nname: Ignored\n---\n", encoding="utf-8")

    snapshot = OpenDesignAdapter().discover(tmp_path)

    assert [skill.normalized_id for skill in snapshot.skills] == ["open_design.kept"]


def test_open_design_discovery_does_not_execute_subprocesses(monkeypatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("Open Design discovery must not launch external runtimes")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    snapshot = OpenDesignAdapter().discover(Path("tests/fixtures/providers/open_design"))

    assert snapshot.skills
