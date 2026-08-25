"""Safety boundaries for the read-only V1 workflow."""

from __future__ import annotations

import http.client
import socket
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree

import pytest
import yaml  # type: ignore[import-untyped]

from skillcord.adapters.base import AdapterContext
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.decisions.store import DecisionStore
from skillcord.discovery.service import DiscoveryService
from skillcord.models.config import ProjectConfig
from skillcord.overlap.aliases import AliasRegistry
from skillcord.overlap.grouping import group_capabilities
from skillcord.status.doctor import Doctor, DoctorContext
from skillcord.sync.planner import SyncContext, SyncPlanner

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROVIDER_ROOT = _REPOSITORY_ROOT / "tests" / "fixtures" / "providers"


def _raise_forbidden_boundary(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("V1 runtime must not access the network")


def _run_read_only_workflow(tmp_path: Path) -> None:
    project_root = copytree(
        _REPOSITORY_ROOT / "tests" / "fixtures" / "projects" / "multi-provider",
        tmp_path / "project",
    )
    project = ProjectConfig.model_validate(
        yaml.safe_load((project_root / ".ai" / "project.yaml").read_text(encoding="utf-8"))
    )
    overrides = DecisionStore(project_root / ".ai" / "overrides.yaml").load()
    discovered = DiscoveryService.default().scan(
        project_root,
        {
            "superpowers": _PROVIDER_ROOT / "superpowers",
            "ecc": _PROVIDER_ROOT / "ecc",
            "open_design": _PROVIDER_ROOT / "open_design",
            "humanizer": _PROVIDER_ROOT / "humanizer",
        },
    )
    skills = tuple(skill for provider in discovered.providers for skill in provider.skills)
    groups = tuple(
        group_capabilities(
            skills,
            AliasRegistry.from_path(
                _REPOSITORY_ROOT / "registry" / "aliases" / "capabilities.yaml"
            ),
            overrides,
        )
    )
    suppressed = {
        skill_id
        for override in overrides.overrides.values()
        for skill_id in override.suppress
    }
    adapter_context = AdapterContext(
        project=project,
        active_skills=tuple(skill for skill in skills if skill.normalized_id not in suppressed),
        decisions=overrides,
    )
    SyncPlanner().plan(
        SyncContext(
            project_root=project_root,
            adapter_context=adapter_context,
            adapters=(ClaudeAdapter(),),
            discovered_skill_ids=frozenset(skill.normalized_id for skill in skills),
            conflict_groups=groups,
            non_interactive=True,
        )
    )
    report = Doctor(clock=lambda: datetime(2026, 8, 11, 17, 0, tzinfo=UTC)).run(
        DoctorContext(
            project=project,
            providers=discovered.providers,
            overrides=overrides,
            conflict_groups=groups,
        )
    )
    assert report.timestamp == datetime(2026, 8, 11, 17, 0, tzinfo=UTC)


def test_read_only_workflow_never_uses_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Discovery through doctor remains local when every HTTP path is unavailable."""

    monkeypatch.setattr(socket.socket, "connect", _raise_forbidden_boundary)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_forbidden_boundary)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", _raise_forbidden_boundary)

    _run_read_only_workflow(tmp_path)
