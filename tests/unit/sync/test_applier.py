import os
import tempfile
from pathlib import Path

import pytest

import skillcord.sync.applier as applier_module
from skillcord.adapters.base import AdapterContext, GeneratedArtifact
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.sync.applier import StaleSyncPlanError, SyncApplier, SyncRollbackError
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


class _MultiFileAdapter:
    harness_id = "multi"

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        return [
            GeneratedArtifact(
                path=Path(name),
                ownership="owned_file",
                content=content,
                source_capability_ids=(),
            )
            for name, content in (
                ("a-existing.txt", b"new a\n"),
                ("b-new.txt", b"new b\n"),
                ("c-fail.txt", b"new c\n"),
            )
        ]


def _plan(tmp_path: Path, *, parent_exists: bool = True):
    if parent_exists:
        (tmp_path / "nested").mkdir()
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


def _multi_plan(tmp_path: Path):
    (tmp_path / "a-existing.txt").write_bytes(b"old a\n")
    (tmp_path / "c-fail.txt").write_bytes(b"old c\n")
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
            adapters=(_MultiFileAdapter(),),
            owned_file_baselines={
                Path("a-existing.txt"): b"old a\n",
                Path("c-fail.txt"): b"old c\n",
            },
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
    target.write_bytes(b"changed after preview\n")

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.read_bytes() == b"changed after preview\n"


def test_apply_rejects_directory_created_at_previously_absent_target(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "nested" / "generated.txt"
    target.mkdir()

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.is_dir()


def test_apply_rechecks_staleness_after_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    target = tmp_path / "nested" / "generated.txt"

    def change_target_after_staging(_target: Path) -> None:
        target.write_bytes(b"changed during staging\n")

    monkeypatch.setattr(
        SyncApplier,
        "_after_stage",
        staticmethod(change_target_after_staging),
        raising=False,
    )

    with pytest.raises(StaleSyncPlanError, match="generated.txt"):
        SyncApplier().apply(plan, approved=True)

    assert target.read_bytes() == b"changed during staging\n"


def test_apply_rejects_parent_swap_before_temp_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    moved_outside_root = tmp_path.parent / f"{tmp_path.name}-stage-parent"

    def move_parent_outside(_target: Path) -> None:
        parent = tmp_path / "nested"
        parent.rename(moved_outside_root)
        parent.mkdir()

    monkeypatch.setattr(
        SyncApplier,
        "_before_stage",
        staticmethod(move_parent_outside),
        raising=False,
    )

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        SyncApplier().apply(plan, approved=True)

    assert list(moved_outside_root.iterdir()) == []
    assert list((tmp_path / "nested").iterdir()) == []


def test_missing_parent_fails_closed_before_created_directory_can_be_substituted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path, parent_exists=False)
    substitute_was_installed = False

    def substitute_created_directory(directory: Path) -> None:
        nonlocal substitute_was_installed
        directory.rmdir()
        directory.mkdir()
        (directory / "attacker.txt").write_bytes(b"substitute\n")
        substitute_was_installed = True

    monkeypatch.setattr(
        SyncApplier,
        "_after_mkdir",
        staticmethod(substitute_created_directory),
        raising=False,
    )

    with pytest.raises(StaleSyncPlanError, match="target parent does not exist"):
        SyncApplier().apply(plan, approved=True)

    assert substitute_was_installed is False
    assert not (tmp_path / "nested").exists()


def test_posix_dirfd_capability_uses_rename_support_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        os,
        "supports_dir_fd",
        {os.rename, os.open, os.unlink},
    )
    monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(os, "fchmod", lambda _descriptor, _mode: None, raising=False)

    assert applier_module._directory_relative_operations_supported() is True


def test_apply_uses_anchored_directory_relative_operations_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    real_replace = os.replace
    real_unlink = os.unlink
    directory_handles: dict[int, Path] = {}
    next_handle = iter(range(100_000, 101_000))
    anchored_replacements: list[tuple[Path, Path]] = []

    def resolved(path: object, dir_fd: int | None) -> Path:
        candidate = Path(os.fspath(path))
        return directory_handles[dir_fd] / candidate if dir_fd is not None else candidate

    def fake_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        target = resolved(path, dir_fd)
        if target.is_dir():
            handle = next(next_handle)
            directory_handles[handle] = target
            return handle
        return real_open(target, flags, mode)

    def fake_fstat(descriptor: int) -> os.stat_result:
        if descriptor in directory_handles:
            return directory_handles[descriptor].stat()
        return real_fstat(descriptor)

    def fake_close(descriptor: int) -> None:
        if descriptor in directory_handles:
            del directory_handles[descriptor]
            return
        real_close(descriptor)

    def fake_replace(
        source: object,
        target: object,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_path = resolved(source, src_dir_fd)
        target_path = resolved(target, dst_dir_fd)
        anchored_replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    def fake_unlink(
        path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_unlink(resolved(path, dir_fd))

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: True)
    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fstat", fake_fstat)
    monkeypatch.setattr(os, "close", fake_close)
    monkeypatch.setattr(os, "replace", fake_replace)
    monkeypatch.setattr(os, "unlink", fake_unlink)
    monkeypatch.setattr(os, "fchmod", lambda _descriptor, _mode: None, raising=False)
    monkeypatch.setattr(os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)

    result = SyncApplier().apply(plan, approved=True)

    assert result.applied is True
    assert (tmp_path / "nested" / "generated.txt").read_bytes() == b"generated\n"
    assert len(anchored_replacements) == 1
    assert anchored_replacements[0][0].parent == tmp_path / "nested"
    assert anchored_replacements[0][0].name.endswith(".skillcord.tmp")
    assert anchored_replacements[0][1] == tmp_path / "nested" / "generated.txt"
    assert directory_handles == {}


def test_apply_rejects_parent_identity_swap_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    moved_outside_root = tmp_path.parent / f"{tmp_path.name}-moved-parent"

    def move_parent_outside(_target: Path) -> None:
        parent = tmp_path / "nested"
        parent.rename(moved_outside_root)
        parent.mkdir()

    monkeypatch.setattr(SyncApplier, "_before_replace", staticmethod(move_parent_outside), raising=False)

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        SyncApplier().apply(plan, approved=True)

    assert not (tmp_path / "nested" / "generated.txt").exists()
    assert not (moved_outside_root / "generated.txt").exists()


def test_later_replace_failure_rolls_back_all_changes_and_plan_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    original_replace = os.replace

    def fail_last_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        if Path(os.fspath(dst)).name == "c-fail.txt":
            raise OSError("injected later replace failure")
        original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_last_replace)

    with pytest.raises(OSError, match="injected later replace failure"):
        SyncApplier().apply(plan, approved=True)

    assert (tmp_path / "a-existing.txt").read_bytes() == b"old a\n"
    assert not (tmp_path / "b-new.txt").exists()
    assert (tmp_path / "c-fail.txt").read_bytes() == b"old c\n"

    monkeypatch.undo()
    result = SyncApplier().apply(plan, approved=True)
    assert result.applied is True
    assert (tmp_path / "a-existing.txt").read_bytes() == b"new a\n"
    assert (tmp_path / "b-new.txt").read_bytes() == b"new b\n"
    assert (tmp_path / "c-fail.txt").read_bytes() == b"new c\n"


def test_rollback_failure_reports_original_and_rollback_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    original_replace = os.replace
    destination_counts: dict[str, int] = {}

    def fail_apply_and_rollback(
        src: object,
        dst: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        name = Path(os.fspath(dst)).name
        destination_counts[name] = destination_counts.get(name, 0) + 1
        if name == "c-fail.txt":
            raise OSError("original apply failure")
        if name == "a-existing.txt" and destination_counts[name] == 2:
            raise OSError("rollback restore failure")
        original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_apply_and_rollback)

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert "original apply failure" in str(raised.value.original_error)
    assert any("rollback restore failure" in str(error) for error in raised.value.rollback_errors)


def test_staging_failure_removes_temporary_from_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("originating staging failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="originating staging failure"):
        SyncApplier().apply(plan, approved=True)

    assert (tmp_path / "nested").is_dir()
    assert list((tmp_path / "nested").iterdir()) == []


def test_fallback_detects_parent_swap_before_writing_staged_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    original_mkstemp = tempfile.mkstemp
    original_verify = applier_module._ParentGuard.verify
    staged_path: Path | None = None
    reject_parent = False

    def swap_after_temp_creation(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal reject_parent, staged_path
        descriptor, temporary_name = original_mkstemp(*args, **kwargs)
        staged_path = Path(temporary_name)
        reject_parent = True
        return descriptor, temporary_name

    def reject_changed_parent(guard: applier_module._ParentGuard) -> None:
        if reject_parent:
            raise StaleSyncPlanError("parent directory changed: simulated swap")
        original_verify(guard)

    monkeypatch.setattr(tempfile, "mkstemp", swap_after_temp_creation)
    monkeypatch.setattr(applier_module._ParentGuard, "verify", reject_changed_parent)

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        SyncApplier().apply(plan, approved=True)

    assert staged_path is not None
    assert staged_path.read_bytes() == b""
    assert not (tmp_path / "nested" / "generated.txt").exists()


def test_fallback_truncates_staged_payload_when_parent_changes_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-stage"
    outside.mkdir()
    actual_staged_path = outside / "redirected.skillcord.tmp"
    lexical_staged_path = tmp_path / "nested" / "redirected.skillcord.tmp"
    original_verify = applier_module._ParentGuard.verify
    reject_parent = False

    def redirected_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        descriptor = os.open(actual_staged_path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        return descriptor, str(lexical_staged_path)

    def mark_parent_changed(_temporary: Path) -> None:
        nonlocal reject_parent
        reject_parent = True

    def reject_changed_parent(guard: applier_module._ParentGuard) -> None:
        if reject_parent:
            raise StaleSyncPlanError("parent directory changed: simulated post-write swap")
        original_verify(guard)

    monkeypatch.setattr(tempfile, "mkstemp", redirected_mkstemp)
    monkeypatch.setattr(
        SyncApplier,
        "_after_stage_write",
        staticmethod(mark_parent_changed),
        raising=False,
    )
    monkeypatch.setattr(applier_module._ParentGuard, "verify", reject_changed_parent)

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        SyncApplier().apply(plan, approved=True)

    assert actual_staged_path.read_bytes() == b""
    assert b"generated" not in actual_staged_path.read_bytes()


def test_fallback_post_stage_parent_swap_leaves_no_payload_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    moved_outside_root = tmp_path.parent / f"{tmp_path.name}-post-stage-parent"

    def move_parent_after_staging(_target: Path) -> None:
        (tmp_path / "nested").rename(moved_outside_root)
        (tmp_path / "nested").mkdir()

    monkeypatch.setattr(
        SyncApplier,
        "_after_stage",
        staticmethod(move_parent_after_staging),
    )

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        SyncApplier().apply(plan, approved=True)

    leaked_payloads = [path.read_bytes() for path in moved_outside_root.iterdir()]
    assert all(b"generated" not in payload for payload in leaked_payloads)
    assert list(tmp_path.glob("*.skillcord.tmp")) == []


def test_anchored_temp_cleanup_uses_retained_directory_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "nested"
    parent.mkdir()
    temporary = parent / ".generated.secret.skillcord.tmp"
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=parent,
        identities=(),
        descriptor=12345,
    )
    unlinked: list[tuple[object, int | None]] = []

    def fail_path_verification() -> None:
        raise StaleSyncPlanError("parent directory changed")

    def record_unlink(path: object, *, dir_fd: int | None = None) -> None:
        unlinked.append((path, dir_fd))

    monkeypatch.setattr(guard, "verify", fail_path_verification)
    monkeypatch.setattr(os, "unlink", record_unlink)

    errors = applier_module._cleanup_paths(guard, [temporary])

    assert errors == []
    assert unlinked == [(temporary.name, 12345)]


def test_fallback_preserves_mode_through_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "nested" / "generated.txt"
    target.parent.mkdir()
    target.write_bytes(b"old generated\n")
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
    descriptor_modes: list[int] = []

    def record_fchmod(_descriptor: int, mode: int) -> None:
        descriptor_modes.append(mode)

    def reject_path_chmod(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pathname chmod must not be used for staged files")

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    monkeypatch.setattr(os, "fchmod", record_fchmod)
    monkeypatch.setattr(os, "chmod", reject_path_chmod)

    SyncApplier().apply(plan, approved=True)

    assert descriptor_modes


def test_cleanup_failure_does_not_mask_staging_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    original_unlink = os.unlink

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("originating staging failure")

    def fail_temp_cleanup(path: object, *args: object, **kwargs: object) -> None:
        if ".skillcord.tmp" in os.fspath(path):
            raise OSError("secondary cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(os, "unlink", fail_temp_cleanup)

    with pytest.raises(OSError, match="originating staging failure") as raised:
        SyncApplier().apply(plan, approved=True)

    assert any("secondary cleanup failure" in note for note in raised.value.__notes__)


def test_success_reports_guard_cleanup_errors_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    original_close = applier_module._ParentGuard.close

    def close_then_fail(guard: applier_module._ParentGuard) -> None:
        original_close(guard)
        raise OSError("injected guard cleanup failure")

    monkeypatch.setattr(applier_module._ParentGuard, "close", close_then_fail)

    result = SyncApplier().apply(plan, approved=True)

    assert result.applied is True
    assert result.cleanup_errors
    assert set(result.cleanup_errors) == {"injected guard cleanup failure"}


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
