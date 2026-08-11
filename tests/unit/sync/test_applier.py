import os
import tempfile
from pathlib import Path

import pytest

from skillcord.adapters.base import AdapterContext, GeneratedArtifact
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.sync.applier import StaleSyncPlanError, SyncApplier
from skillcord.sync.planner import SyncContext, SyncPlanner


class _StaticAdapter:
    harness_id = "static"

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        return [
            GeneratedArtifact(
                path=Path("nested/generated.txt"),
                ownership="owned_file",
                content=b"generated\n",
                source_capability_ids=(),
            )
        ]


def _plan(tmp_path: Path):
    adapter_context = AdapterContext(
        project=ProjectConfig(
            schema_version=1,
            project=ProjectInfo(intent="api"),
            ai=AIConfig(),
        ),
        active_skills=(),
        decisions=OverrideConfig(schema_version=1),
    )
    return SyncPlanner().plan(
        SyncContext(
            project_root=tmp_path,
            adapter_context=adapter_context,
            adapters=(_StaticAdapter(),),
        )
    )


def test_apply_requires_approval_and_leaves_plan_unchanged(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    result = SyncApplier().apply(plan, approved=False)

    assert result.applied is False
    assert result.changed_files == ()
    assert not (tmp_path / "nested" / "generated.txt").exists()


def test_apply_writes_only_precomputed_bytes_after_approval(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    result = SyncApplier().apply(plan, approved=True)

    target = tmp_path / "nested" / "generated.txt"
    assert result.applied is True
    assert result.changed_files == (target,)
    assert target.read_bytes() == b"generated\n"


def test_apply_fails_before_any_write_when_plan_is_stale(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "nested" / "generated.txt"
    target.parent.mkdir()
    target.write_bytes(b"changed after preview\n")

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.read_bytes() == b"changed after preview\n"


def test_apply_rejects_directory_created_at_previously_absent_target(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "nested" / "generated.txt"
    target.mkdir(parents=True)

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.is_dir()


def test_apply_rechecks_staleness_after_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "nested" / "generated.txt"
    original_mkstemp = tempfile.mkstemp

    def change_target_after_staging(*args: object, **kwargs: object) -> tuple[int, str]:
        result = original_mkstemp(*args, **kwargs)
        target.write_bytes(b"changed during staging\n")
        return result

    monkeypatch.setattr(tempfile, "mkstemp", change_target_after_staging)

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.read_bytes() == b"changed during staging\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_apply_preserves_existing_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "generated.txt"
    target.parent.mkdir()
    target.write_bytes(b"old generated\n")
    target.chmod(0o640)

    # The existing content is explicitly proven to be previously generated.
    plan = SyncPlanner().plan(
        SyncContext(
            project_root=tmp_path,
            adapter_context=AdapterContext(
                project=ProjectConfig(
                    schema_version=1,
                    project=ProjectInfo(intent="api"),
                    ai=AIConfig(),
                ),
                active_skills=(),
                decisions=OverrideConfig(schema_version=1),
            ),
            adapters=(_StaticAdapter(),),
            owned_file_baselines={Path("nested/generated.txt"): b"old generated\n"},
        )
    )

    SyncApplier().apply(plan, approved=True)

    assert target.stat().st_mode & 0o777 == 0o640
