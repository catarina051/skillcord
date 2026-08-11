"""Tests for safe, partial ECC provider discovery."""

import subprocess
from pathlib import Path

from skillcord.providers.ecc import ECCAdapter


def test_ecc_discovers_declarative_skills_and_reports_hooks() -> None:
    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))

    assert {skill.normalized_id for skill in snapshot.skills} == {"ecc.security-review"}
    assert snapshot.partial_support is True
    assert snapshot.unsupported_assets[0].kind == "executable-hook"
    assert snapshot.unsupported_assets[0].source_path == (
        Path("tests/fixtures/providers/ecc/hooks/hooks.json").resolve()
    )
    assert snapshot.unsupported_assets[0].reason == (
        "ECC executable hooks are unsupported and remain inactive in V1"
    )


def test_ecc_root_component_hashes_all_discovered_skills() -> None:
    snapshot = ECCAdapter().discover(Path("tests/fixtures/providers/ecc"))

    root_component = next(component for component in snapshot.components if component.component_id == "ecc")
    assert root_component.artifact_hashes == {
        skill.source_path: skill.content_hash for skill in snapshot.skills
    }


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
