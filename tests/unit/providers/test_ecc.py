"""Tests for safe, partial ECC provider discovery."""

import subprocess
from pathlib import Path

from skillcord.providers.ecc import ECCAdapter


def test_ecc_discovers_declarative_skills_and_reports_hooks() -> None:
    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))

    assert {skill.normalized_id for skill in snapshot.skills} == {"ecc.security-review"}
    assert snapshot.partial_support is True
    assert any(asset.kind == "executable-hook" for asset in snapshot.unsupported_assets)


def test_ecc_discovery_does_not_execute_subprocesses(monkeypatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ECC discovery must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))

    assert [skill.normalized_id for skill in snapshot.skills] == ["ecc.security-review"]


def test_ecc_discovers_declarative_skills_at_supported_nested_paths(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "security" / "review" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: Nested Security Review\n---\n", encoding="utf-8")

    snapshot = ECCAdapter().discover(tmp_path)

    assert [skill.normalized_id for skill in snapshot.skills] == ["ecc.nested-security-review"]


def test_ecc_records_only_safely_parsed_agent_and_command_metadata() -> None:
    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))

    assert {component.component_id for component in snapshot.components} == {
        "ecc",
        "ecc.agent.security-reviewer",
        "ecc.command.security-review",
    }
