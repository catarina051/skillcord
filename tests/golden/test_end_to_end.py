"""Reviewed, deterministic bytes for the complete V1 fixture flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree

import pytest
import yaml  # type: ignore[import-untyped]

from skillcord.adapters.base import AdapterContext, compose_artifacts
from skillcord.adapters.claude import ClaudeAdapter
from skillcord.decisions.store import DecisionStore
from skillcord.discovery.service import DiscoveryService
from skillcord.locking.service import LockService
from skillcord.models.config import ProjectConfig
from skillcord.overlap.aliases import AliasRegistry
from skillcord.overlap.grouping import group_capabilities
from skillcord.status.doctor import Doctor, DoctorContext
from skillcord.status.serializer import serialize_status
from skillcord.sync.applier import SyncApplier
from skillcord.sync.planner import SyncContext, SyncPlanner

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "tests" / "fixtures" / "projects"
_PROVIDER_ROOT = _REPOSITORY_ROOT / "tests" / "fixtures" / "providers"
_EXPECTED_ROOT = _REPOSITORY_ROOT / "tests" / "golden" / "expected"


@dataclass(frozen=True)
class GoldenOutput:
    agents_md: bytes
    claude_md: bytes
    lock_yaml: bytes
    status_json: bytes


class _NoRevisionResolver:
    def resolve(self, path: Path) -> None:
        del path


class GoldenRunner:
    """Run the full V1 flow against a disposable project copy."""

    def __init__(self, temporary_root: Path) -> None:
        self._temporary_root = temporary_root

    def run(self, fixture: str) -> GoldenOutput:
        source = _REPOSITORY_ROOT / fixture
        project_root = copytree(source, self._temporary_root / "project")
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
                AliasRegistry.from_path(_REPOSITORY_ROOT / "registry" / "aliases" / "capabilities.yaml"),
                overrides,
            )
        )
        suppressed = {
            skill_id
            for override in overrides.overrides.values()
            for skill_id in override.suppress
        }
        active_skills = tuple(skill for skill in skills if skill.normalized_id not in suppressed)
        adapter_context = AdapterContext(
            project=project,
            active_skills=active_skills,
            decisions=overrides,
        )
        adapters = (ClaudeAdapter(),)
        plan = SyncPlanner().plan(
            SyncContext(
                project_root=project_root,
                adapter_context=adapter_context,
                adapters=adapters,
                discovered_skill_ids=frozenset(skill.normalized_id for skill in skills),
                conflict_groups=groups,
                non_interactive=True,
            )
        )
        SyncApplier().apply(plan, approved=True)
        lock = LockService(revision_resolver=_NoRevisionResolver()).build(
            discovered.providers,
            {
                skill_id
                for override in overrides.overrides.values()
                for skill_id in ([override.prefer] if override.prefer is not None else [])
                + override.suppress
            },
        )
        report = Doctor(clock=lambda: datetime(2026, 8, 11, 17, 0, tzinfo=UTC)).run(
            DoctorContext(
                project=project,
                providers=discovered.providers,
                harnesses=DiscoveryService.default().scan(project_root, {}).harnesses,
                required_provider_ids=frozenset(provider.provider_id for provider in discovered.providers),
                lock=lock,
                overrides=overrides,
                conflict_groups=groups,
                project_root=project_root,
                adapter_artifacts=compose_artifacts(adapters, adapter_context),
            )
        )
        return GoldenOutput(
            agents_md=(project_root / "AGENTS.md").read_bytes(),
            claude_md=(project_root / "CLAUDE.md").read_bytes(),
            lock_yaml=LockService(revision_resolver=_NoRevisionResolver()).serialize(lock),
            status_json=serialize_status(report).encode("utf-8"),
        )


@pytest.fixture
def golden_runner(tmp_path: Path) -> GoldenRunner:
    return GoldenRunner(tmp_path)


def read_bytes(path: str) -> bytes:
    return (_REPOSITORY_ROOT / path).read_bytes()


def test_multi_provider_project_matches_reviewed_golden(golden_runner: GoldenRunner) -> None:
    output = golden_runner.run("tests/fixtures/projects/multi-provider")
    assert output.agents_md == read_bytes("tests/golden/expected/AGENTS.md")
    assert output.claude_md == read_bytes("tests/golden/expected/CLAUDE.md")
    assert output.lock_yaml == read_bytes("tests/golden/expected/ai-lock.yaml")
    assert output.status_json == read_bytes("tests/golden/expected/status.json")
