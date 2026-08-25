"""Regression tests for V1's no-host-mutation boundary."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from skillcord.adapters.base import AdapterContext
from skillcord.discovery.service import DiscoveryService
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.status.doctor import Doctor, DoctorContext
from skillcord.sync.planner import SyncContext, SyncPlanner

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_INSTALL_COMMANDS = (
    "winget install",
    "apt install",
    "brew install",
    "dnf install",
    "pacman -s",
    "curl | bash",
)


def _raise_forbidden_process(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("V1 runtime must not execute provider hooks or host commands")


def test_discovery_sync_planning_and_doctor_never_execute_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real read-only path works even when all process launch APIs reject calls."""

    monkeypatch.setattr(subprocess, "run", _raise_forbidden_process)
    monkeypatch.setattr(subprocess, "Popen", _raise_forbidden_process)
    monkeypatch.setattr(os, "system", _raise_forbidden_process)

    discovered = DiscoveryService.default().scan(
        tmp_path,
        {
            "superpowers": _REPOSITORY_ROOT / "tests" / "fixtures" / "providers" / "superpowers",
            "ecc": _REPOSITORY_ROOT / "tests" / "fixtures" / "providers" / "ecc",
        },
    )
    ecc = next(provider for provider in discovered.providers if provider.provider_id == "ecc")
    assert [(asset.kind, asset.reason) for asset in ecc.unsupported_assets] == [
        ("executable-hook", "ECC executable hooks are unsupported and remain inactive in V1")
    ]
    project = ProjectConfig(
        schema_version=1,
        project=ProjectInfo(intent="safety test"),
        ai=AIConfig(harnesses=[]),
    )
    SyncPlanner().plan(
        SyncContext(
            project_root=tmp_path,
            adapter_context=AdapterContext(
                project=project,
                active_skills=tuple(discovered.providers[0].skills),
                decisions=OverrideConfig(schema_version=1),
            ),
            adapters=(),
        )
    )
    report = Doctor().run(DoctorContext(project=project, providers=discovered.providers))
    assert report.checks


def test_executable_runtime_source_never_orchestrates_host_package_installation() -> None:
    """A future install command in V1 executable source is a safety regression."""

    source_text = "\n".join(
        source_path.read_text(encoding="utf-8").lower()
        for source_path in sorted((_REPOSITORY_ROOT / "src" / "skillcord").rglob("*.py"))
    )

    assert all(command not in source_text for command in _FORBIDDEN_INSTALL_COMMANDS)


def test_coverage_gate_enforces_v1_core_line_coverage() -> None:
    """CI must fail when the covered V1 core drops below the release threshold."""

    configuration = tomllib.loads((_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["tool"]["coverage"]["report"]["fail_under"] == 90
    assert set(configuration["tool"]["coverage"]["report"]["include"]) == {
        "src/skillcord/providers/*",
        "src/skillcord/overlap/*",
        "src/skillcord/decisions/*",
        "src/skillcord/locking/*",
        "src/skillcord/managed_blocks/*",
        "src/skillcord/sync/*",
        "src/skillcord/status/*",
    }
