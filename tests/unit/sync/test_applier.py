import hashlib
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import skillcord.sync.applier as applier_module
from skillcord.adapters.base import AdapterContext, GeneratedArtifact
from skillcord.models.config import AIConfig, OverrideConfig, ProjectConfig, ProjectInfo
from skillcord.sync.applier import StaleSyncPlanError, SyncApplier, SyncRollbackError
from skillcord.sync.planner import PlannedFileChange, SyncContext, SyncPlanner


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


class _FakeCFunction:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.result


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


def _staged_entry(path: Path) -> applier_module._StagedEntry:
    metadata = path.stat()
    return applier_module._StagedEntry(
        path=path,
        identity=applier_module._FileIdentity.from_stat(metadata),
        size=metadata.st_size,
        mode=metadata.st_mode & 0o7777,
        content_hash=hashlib.sha256(path.read_bytes()).digest(),
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


def test_replacement_stage_mode_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    tampered: Path | None = None

    def tamper_replacement_mode(temporary: Path) -> None:
        nonlocal tampered
        if not temporary.name.endswith(".skillcord.tmp"):
            return
        original_mode = stat.S_IMODE(temporary.stat().st_mode)
        temporary.chmod(0o444 if original_mode != 0o444 else 0o666)
        assert stat.S_IMODE(temporary.stat().st_mode) != original_mode
        tampered = temporary

    monkeypatch.setattr(
        SyncApplier,
        "_after_stage_write",
        staticmethod(tamper_replacement_mode),
    )

    with pytest.raises(StaleSyncPlanError, match="staged file mode changed"):
        SyncApplier().apply(plan, approved=True)

    assert tampered is not None
    assert not (tmp_path / "nested" / "generated.txt").exists()


def test_backup_stage_mode_tampering_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    tampered: Path | None = None

    def tamper_backup_mode(temporary: Path) -> None:
        nonlocal tampered
        if not temporary.name.endswith(".skillcord.backup"):
            return
        original_mode = stat.S_IMODE(temporary.stat().st_mode)
        temporary.chmod(0o444 if original_mode != 0o444 else 0o666)
        assert stat.S_IMODE(temporary.stat().st_mode) != original_mode
        tampered = temporary

    monkeypatch.setattr(
        SyncApplier,
        "_after_stage_write",
        staticmethod(tamper_backup_mode),
    )

    with pytest.raises(StaleSyncPlanError, match="staged file mode changed"):
        SyncApplier().apply(plan, approved=True)

    assert tampered is not None
    assert (tmp_path / "a-existing.txt").read_bytes() == b"old a\n"
    assert not (tmp_path / "b-new.txt").exists()
    assert (tmp_path / "c-fail.txt").read_bytes() == b"old c\n"


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


def test_anchored_post_replace_parent_move_rolls_back_through_retained_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "nested"
    parent.mkdir()
    target = parent / "generated.txt"
    replacement = parent / ".generated.skillcord.tmp"
    backup = parent / ".generated.skillcord.backup"
    target.write_bytes(b"old\n")
    replacement.write_bytes(b"generated secret\n")
    backup.write_bytes(b"old\n")
    moved_parent = tmp_path.parent / f"{tmp_path.name}-anchored-parent"
    descriptor = 12345
    real_open = os.open
    real_fstat = os.fstat
    real_replace = os.replace

    def anchored_path(name: object) -> Path:
        base = moved_parent if moved_parent.exists() else parent
        return base / Path(os.fspath(name))

    def fake_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd == descriptor:
            return real_open(anchored_path(path), flags, mode)
        return real_open(path, flags, mode)

    def fake_fstat(fd: int) -> os.stat_result:
        if fd == descriptor:
            return (moved_parent if moved_parent.exists() else parent).stat()
        return real_fstat(fd)

    first_replace = True

    def fake_replace(
        source: object,
        destination: object,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal first_replace
        source_path = anchored_path(source) if src_dir_fd == descriptor else Path(source)
        destination_path = (
            anchored_path(destination) if dst_dir_fd == descriptor else Path(destination)
        )
        real_replace(source_path, destination_path)
        if first_replace:
            first_replace = False
            parent.rename(moved_parent)
            parent.mkdir()

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fstat", fake_fstat)
    monkeypatch.setattr(os, "replace", fake_replace)

    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=parent,
        identities=applier_module._capture_parent_chain(tmp_path, parent),
        descriptor=descriptor,
    )
    item = applier_module._StagedChange(
        change=PlannedFileChange(
            path=target,
            relative_path=Path("nested/generated.txt"),
            ownership="owned_file",
            before=b"old\n",
            after=b"generated secret\n",
        ),
        guard=guard,
        staging_guard=guard,
        replacement=_staged_entry(replacement),
        backup=_staged_entry(backup),
        original=_staged_entry(target),
    )

    with pytest.raises(StaleSyncPlanError, match="parent directory changed"):
        applier_module._replace_staged(item, item.replacement, target)

    rollback_errors = SyncApplier._rollback([item])

    assert rollback_errors == []
    assert (moved_parent / "generated.txt").read_bytes() == b"old\n"
    assert b"generated secret" not in (moved_parent / "generated.txt").read_bytes()
    assert not target.exists()


def test_replacement_stage_substitution_is_never_installed_or_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    substituted: Path | None = None

    def substitute_replacement(_target: Path) -> None:
        nonlocal substituted
        replacement = next(tmp_path.glob("*.skillcord.tmp"))
        replacement.unlink()
        replacement.write_bytes(b"attacker replacement\n")
        substituted = replacement

    monkeypatch.setattr(
        SyncApplier,
        "_before_replace",
        staticmethod(substitute_replacement),
    )

    with pytest.raises(StaleSyncPlanError, match="staged file changed"):
        SyncApplier().apply(plan, approved=True)

    assert not (tmp_path / "nested" / "generated.txt").exists()
    assert substituted is not None
    assert not substituted.exists()
    preserved = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and path.read_bytes() == b"attacker replacement\n"
    ]
    assert len(preserved) == 1
    assert preserved[0].name.endswith(".skillcord.quarantine")


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


def test_backup_substitution_is_never_used_or_deleted_during_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    substituted: Path | None = None

    def substitute_backup_and_stale_last(target: Path) -> None:
        nonlocal substituted
        if target.name != "c-fail.txt":
            return
        backup = next(
            path
            for path in tmp_path.glob("*.skillcord.backup")
            if path.read_bytes() == b"old a\n"
        )
        backup.unlink()
        backup.write_bytes(b"attacker backup\n")
        substituted = backup
        target.write_bytes(b"concurrent change\n")

    monkeypatch.setattr(
        SyncApplier,
        "_before_replace",
        staticmethod(substitute_backup_and_stale_last),
    )

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert any("staged file changed" in str(error) for error in raised.value.rollback_errors)
    assert (tmp_path / "a-existing.txt").read_bytes() == b"new a\n"
    assert substituted is not None
    assert substituted.read_bytes() == b"attacker backup\n"


def test_same_content_installed_target_substitution_is_not_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    substituted_identity: tuple[int, int] | None = None

    def substitute_installed_target_and_stale_last(target: Path) -> None:
        nonlocal substituted_identity
        if target.name != "c-fail.txt":
            return
        installed = tmp_path / "a-existing.txt"
        installed.unlink()
        installed.write_bytes(b"new a\n")
        metadata = installed.stat()
        substituted_identity = (metadata.st_dev, metadata.st_ino)
        target.write_bytes(b"concurrent change\n")

    monkeypatch.setattr(
        SyncApplier,
        "_before_replace",
        staticmethod(substitute_installed_target_and_stale_last),
    )

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert any("installed file changed" in str(error) for error in raised.value.rollback_errors)
    assert (tmp_path / "a-existing.txt").read_bytes() == b"new a\n"
    metadata = (tmp_path / "a-existing.txt").stat()
    assert substituted_identity == (metadata.st_dev, metadata.st_ino)


def test_same_original_bytes_substitute_blocks_rollback_and_preserves_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    substituted_identity: tuple[int, int] | None = None
    recovery_backup: Path | None = None

    def substitute_original_bytes_and_stale_last(target: Path) -> None:
        nonlocal recovery_backup, substituted_identity
        if target.name != "c-fail.txt":
            return
        recovery_backup = next(
            path
            for path in tmp_path.glob("*.skillcord.backup")
            if path.read_bytes() == b"old a\n"
        )
        installed = tmp_path / "a-existing.txt"
        installed.unlink()
        installed.write_bytes(b"old a\n")
        metadata = installed.stat()
        substituted_identity = (metadata.st_dev, metadata.st_ino)
        target.write_bytes(b"concurrent change\n")

    monkeypatch.setattr(
        SyncApplier,
        "_before_replace",
        staticmethod(substitute_original_bytes_and_stale_last),
    )

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert any("installed file changed" in str(error) for error in raised.value.rollback_errors)
    installed = tmp_path / "a-existing.txt"
    assert installed.read_bytes() == b"old a\n"
    metadata = installed.stat()
    assert substituted_identity == (metadata.st_dev, metadata.st_ino)
    assert recovery_backup is not None
    assert recovery_backup.read_bytes() == b"old a\n"


def test_restore_rechecks_target_after_backup_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    original_verify_entry = applier_module._verify_entry_at
    rollback_started = False
    substituted_identity: tuple[int, int] | None = None

    def stale_last_target(target: Path) -> None:
        nonlocal rollback_started
        if target.name == "c-fail.txt":
            rollback_started = True
            target.write_bytes(b"concurrent change\n")

    def substitute_after_backup_verification(
        guard: applier_module._ParentGuard,
        path: Path,
        expected: applier_module._StagedEntry,
        *,
        trusted: bool,
        label: str = "staged file changed",
    ) -> None:
        nonlocal substituted_identity
        original_verify_entry(
            guard,
            path,
            expected,
            trusted=trusted,
            label=label,
        )
        if (
            rollback_started
            and path.name.endswith(".skillcord.backup")
            and expected.content_hash == hashlib.sha256(b"old a\n").digest()
            and substituted_identity is None
        ):
            installed = tmp_path / "a-existing.txt"
            installed.unlink()
            installed.write_bytes(b"new a\n")
            metadata = installed.stat()
            substituted_identity = (metadata.st_dev, metadata.st_ino)

    monkeypatch.setattr(SyncApplier, "_before_replace", staticmethod(stale_last_target))
    monkeypatch.setattr(applier_module, "_verify_entry_at", substitute_after_backup_verification)

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert any("installed file changed" in str(error) for error in raised.value.rollback_errors)
    assert (tmp_path / "a-existing.txt").read_bytes() == b"new a\n"
    metadata = (tmp_path / "a-existing.txt").stat()
    assert substituted_identity == (metadata.st_dev, metadata.st_ino)


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


def test_fallback_cleanup_quarantines_substitution_instead_of_deleting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    real_rename_noreplace = applier_module._atomic_rename_noreplace
    attacker_bytes = b"cleanup substitute\n"
    substitution_happened = False

    def fail_after_stage(_target: Path) -> None:
        raise OSError("injected post-stage failure")

    def substitute_before_quarantine(
        guarded: applier_module._ParentGuard,
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal substitution_happened
        if source.name.endswith(".skillcord.tmp"):
            source.unlink()
            source.write_bytes(attacker_bytes)
            substitution_happened = True
        real_rename_noreplace(guarded, source, destination)

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    monkeypatch.setattr(SyncApplier, "_after_stage", staticmethod(fail_after_stage))
    monkeypatch.setattr(
        applier_module,
        "_atomic_rename_noreplace",
        substitute_before_quarantine,
    )

    with pytest.raises(OSError, match="injected post-stage failure") as raised:
        SyncApplier().apply(plan, approved=True)

    assert substitution_happened is True
    assert any(
        "staged file changed; refusing cleanup" in note
        for note in getattr(raised.value, "__notes__", ())
    )
    preserved = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and path.read_bytes() == attacker_bytes
    ]
    assert len(preserved) == 1
    assert "skillcord.quarantine" in preserved[0].name
    assert not (tmp_path / "nested" / "generated.txt").exists()


def test_fallback_new_target_rollback_quarantines_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _multi_plan(tmp_path)
    real_rename_noreplace = applier_module._atomic_rename_noreplace
    attacker_bytes = b"rollback substitute\n"
    substitution_happened = False

    def stale_last_target(target: Path) -> None:
        if target.name == "c-fail.txt":
            target.write_bytes(b"concurrent change\n")

    def substitute_new_target_before_quarantine(
        guarded: applier_module._ParentGuard,
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal substitution_happened
        if source == tmp_path / "b-new.txt":
            source.unlink()
            source.write_bytes(attacker_bytes)
            substitution_happened = True
        real_rename_noreplace(guarded, source, destination)

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    monkeypatch.setattr(SyncApplier, "_before_replace", staticmethod(stale_last_target))
    monkeypatch.setattr(
        applier_module,
        "_atomic_rename_noreplace",
        substitute_new_target_before_quarantine,
    )

    with pytest.raises(SyncRollbackError) as raised:
        SyncApplier().apply(plan, approved=True)

    assert substitution_happened is True
    assert any("installed file changed" in str(error) for error in raised.value.rollback_errors)
    preserved = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and path.read_bytes() == attacker_bytes
    ]
    assert len(preserved) == 1
    assert "skillcord.quarantine" in preserved[0].name
    assert not (tmp_path / "b-new.txt").exists()
    assert (tmp_path / "a-existing.txt").read_bytes() == b"old a\n"


def test_fallback_quarantine_retries_raced_destination_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / ".generated.skillcord.tmp"
    temporary.write_bytes(b"staged\n")
    entry = _staged_entry(temporary)
    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)
    real_rename_noreplace = applier_module._atomic_rename_noreplace
    raced_destination: Path | None = None
    rename_attempts = 0
    tokens = iter(("raced", "fresh"))

    def race_first_destination(
        guarded: applier_module._ParentGuard,
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal raced_destination, rename_attempts
        rename_attempts += 1
        if rename_attempts == 1:
            raced_destination = destination
            raced_destination.write_bytes(b"unowned collision\n")
        real_rename_noreplace(guarded, source, destination)

    monkeypatch.setattr(applier_module.secrets, "token_hex", lambda _size: next(tokens))
    monkeypatch.setattr(applier_module, "_atomic_rename_noreplace", race_first_destination)

    errors = applier_module._cleanup_entries(guard, [entry])
    guard.close()

    assert rename_attempts == 2
    assert raced_destination is not None
    assert raced_destination.read_bytes() == b"unowned collision\n"
    assert not temporary.exists()
    assert errors == []


@pytest.mark.skipif(os.name != "posix", reason="requires real POSIX rename semantics")
def test_posix_quarantine_move_never_overwrites_real_collision(tmp_path: Path) -> None:
    source = tmp_path / ".generated.skillcord.tmp"
    collision = tmp_path / ".generated.collision.skillcord.quarantine"
    source.write_bytes(b"staged\n")
    collision.write_bytes(b"unowned collision\n")
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)

    try:
        with pytest.raises(FileExistsError):
            applier_module._rename_to_quarantine(
                guard,
                source,
                collision,
                trusted=guard.descriptor is not None,
            )
    finally:
        guard.close()

    assert source.read_bytes() == b"staged\n"
    assert collision.read_bytes() == b"unowned collision\n"


@pytest.mark.parametrize(
    ("system_name", "helper_name"),
    (("Linux", "_linux_rename_noreplace"), ("Darwin", "_macos_rename_noreplace")),
)
def test_atomic_no_replace_dispatches_only_to_supported_posix_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system_name: str,
    helper_name: str,
) -> None:
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )
    calls: list[tuple[Path, Path]] = []

    def record_call(
        _guard: applier_module._ParentGuard,
        source: Path,
        destination: Path,
    ) -> None:
        calls.append((source, destination))

    monkeypatch.setattr(applier_module.os, "name", "posix")
    monkeypatch.setattr(applier_module.platform, "system", lambda: system_name)
    monkeypatch.setattr(applier_module, helper_name, record_call)
    source = tmp_path / "source"
    destination = tmp_path / "destination"

    applier_module._atomic_rename_noreplace(guard, source, destination)

    assert calls == [(source, destination)]


def test_atomic_no_replace_fails_closed_on_unknown_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"owned\n")
    destination = tmp_path / "destination"
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )
    monkeypatch.setattr(applier_module.os, "name", "posix")
    monkeypatch.setattr(applier_module.platform, "system", lambda: "unsupported")

    with pytest.raises(StaleSyncPlanError, match="preserved source entry"):
        applier_module._atomic_rename_noreplace(guard, source, destination)

    assert source.read_bytes() == b"owned\n"
    assert not destination.exists()


def test_linux_renameat2_wrapper_uses_exclusive_dirfd_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renameat2 = _FakeCFunction()
    monkeypatch.setattr(
        applier_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=renameat2),
    )
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
        descriptor=456,
    )

    applier_module._linux_rename_noreplace(
        guard,
        tmp_path / "source",
        tmp_path / "destination",
    )

    assert renameat2.calls == [(456, b"source", 456, b"destination", 1)]


def test_linux_renameat2_syscall_fallback_uses_known_architecture_number(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    syscall = _FakeCFunction()
    monkeypatch.setattr(
        applier_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(syscall=syscall),
    )
    monkeypatch.setattr(applier_module.platform, "machine", lambda: "x86_64")
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )

    applier_module._linux_rename_noreplace(
        guard,
        tmp_path / "source",
        tmp_path / "destination",
    )

    assert len(syscall.calls) == 1
    call = syscall.calls[0]
    assert call[0].value == 316
    assert call[1].value == -100
    assert call[2].value == os.fsencode(tmp_path / "source")
    assert call[3].value == -100
    assert call[4].value == os.fsencode(tmp_path / "destination")
    assert call[5].value == 1


def test_linux_renameat2_fails_closed_without_known_syscall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        applier_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(syscall=_FakeCFunction()),
    )
    monkeypatch.setattr(applier_module.platform, "machine", lambda: "unknown")
    source = tmp_path / "source"
    source.write_bytes(b"owned\n")
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )

    with pytest.raises(StaleSyncPlanError, match="preserved source entry"):
        applier_module._linux_rename_noreplace(
            guard,
            source,
            tmp_path / "destination",
        )

    assert source.read_bytes() == b"owned\n"


def test_macos_no_replace_wrappers_use_exclusive_flag_and_dirfd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renameatx_np = _FakeCFunction()
    renamex_np = _FakeCFunction()
    monkeypatch.setattr(
        applier_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(
            renameatx_np=renameatx_np,
            renamex_np=renamex_np,
        ),
    )
    anchored_guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
        descriptor=789,
    )
    fallback_guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )
    source = tmp_path / "source"
    destination = tmp_path / "destination"

    applier_module._macos_rename_noreplace(anchored_guard, source, destination)
    applier_module._macos_rename_noreplace(fallback_guard, source, destination)

    assert renameatx_np.calls == [(789, b"source", 789, b"destination", 0x4)]
    assert renamex_np.calls == [
        (os.fsencode(source), os.fsencode(destination), 0x4)
    ]


def test_macos_no_replace_fails_closed_without_safe_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        applier_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    source = tmp_path / "source"
    source.write_bytes(b"owned\n")
    guard = applier_module._ParentGuard(
        root=tmp_path,
        parent=tmp_path,
        identities=(),
    )

    with pytest.raises(StaleSyncPlanError, match="preserved source entry"):
        applier_module._macos_rename_noreplace(
            guard,
            source,
            tmp_path / "destination",
        )

    assert source.read_bytes() == b"owned\n"


def test_posix_fallback_delete_revalidates_then_removes_verified_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarantine = tmp_path / ".generated.skillcord.quarantine"
    quarantine.write_bytes(b"staged\n")
    expected = _staged_entry(quarantine)
    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)
    monkeypatch.setattr(applier_module.os, "name", "posix")

    applier_module._delete_verified_quarantine(
        guard,
        quarantine,
        expected,
        trusted=False,
        label="staged file changed; refusing cleanup",
    )
    guard.close()

    assert not quarantine.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits unlinking an open file")
def test_fallback_observation_rejects_entry_substituted_during_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".generated.skillcord.tmp"
    target.write_bytes(b"staged\n")
    expected = _staged_entry(target)
    attacker_bytes = b"substitute with the same size\n"
    substituted = False
    real_sha256 = hashlib.sha256

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)

    def substitute_during_hash(content: bytes = b""):
        nonlocal substituted
        if content == b"staged\n" and not substituted:
            target.unlink()
            target.write_bytes(attacker_bytes)
            substituted = True
        return real_sha256(content)

    monkeypatch.setattr(applier_module.hashlib, "sha256", substitute_during_hash)

    try:
        with pytest.raises(StaleSyncPlanError, match="staged file changed"):
            applier_module._verify_entry_at(
                guard,
                target,
                expected,
                trusted=False,
            )
    finally:
        guard.close()

    assert substituted is True
    assert target.read_bytes() == attacker_bytes


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits unlinking an open file")
def test_anchored_observation_rejects_entry_substituted_during_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".generated.skillcord.tmp"
    target.write_bytes(b"staged\n")
    expected = _staged_entry(target)
    attacker_bytes = b"anchored substitute\n"
    substituted = False
    real_sha256 = hashlib.sha256
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)
    if guard.descriptor is None:
        pytest.skip("directory-relative operations are unavailable")

    def substitute_during_hash(content: bytes = b""):
        nonlocal substituted
        if content == b"staged\n" and not substituted:
            target.unlink()
            target.write_bytes(attacker_bytes)
            substituted = True
        return real_sha256(content)

    monkeypatch.setattr(applier_module.hashlib, "sha256", substitute_during_hash)

    try:
        with pytest.raises(StaleSyncPlanError, match="staged file changed"):
            applier_module._verify_entry_at(
                guard,
                target,
                expected,
                trusted=True,
            )
    finally:
        guard.close()

    assert substituted is True
    assert target.read_bytes() == attacker_bytes


def test_fallback_cleanup_preserves_substitute_installed_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / ".generated.skillcord.tmp"
    temporary.write_bytes(b"staged\n")
    entry = _staged_entry(temporary)
    attacker_bytes = b"fallback delete substitute\n"
    substituted = False
    real_verify = applier_module._verify_entry_at

    monkeypatch.setattr(applier_module, "_directory_relative_operations_supported", lambda: False)
    monkeypatch.setattr(applier_module.secrets, "token_hex", lambda _size: "fallback-delete")
    guard = applier_module._ParentGuard.open(tmp_path, tmp_path)

    def verify_then_substitute(
        guarded: applier_module._ParentGuard,
        path: Path,
        expected: applier_module._StagedEntry,
        *,
        trusted: bool,
        label: str = "staged file changed",
    ) -> None:
        nonlocal substituted
        real_verify(guarded, path, expected, trusted=trusted, label=label)
        if path.name.endswith(".skillcord.quarantine") and not substituted:
            path.unlink()
            path.write_bytes(attacker_bytes)
            substituted = True

    monkeypatch.setattr(applier_module, "_verify_entry_at", verify_then_substitute)

    errors = applier_module._cleanup_entries(guard, [entry])
    guard.close()

    assert substituted is True
    assert len(errors) == 1
    assert "refusing" in str(errors[0])
    preserved = [path for path in tmp_path.iterdir() if path.read_bytes() == attacker_bytes]
    assert len(preserved) == 1
    assert preserved[0].name.endswith(".skillcord.quarantine")


def test_anchored_cleanup_preserves_substitute_installed_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "nested"
    parent.mkdir()
    temporary = parent / ".generated.skillcord.tmp"
    temporary.write_bytes(b"staged\n")
    entry = _staged_entry(temporary)
    attacker_bytes = b"anchored delete substitute\n"
    substituted = False
    real_verify = applier_module._verify_entry_at
    real_open = os.open
    real_rename = os.rename
    real_unlink = os.unlink

    guard = applier_module._ParentGuard.open(tmp_path, parent)
    simulated_descriptor = guard.descriptor is None
    if simulated_descriptor:
        guard.descriptor = 12345

        def resolved(path: object, dir_fd: int | None) -> Path:
            candidate = Path(os.fspath(path))
            return parent / candidate if dir_fd == guard.descriptor else candidate

        def anchored_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            return real_open(resolved(path, dir_fd), flags, mode)

        def anchored_rename(
            source: object,
            destination: object,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            real_rename(resolved(source, src_dir_fd), resolved(destination, dst_dir_fd))

        def anchored_unlink(path: object, *, dir_fd: int | None = None) -> None:
            real_unlink(resolved(path, dir_fd))

        monkeypatch.setattr(os, "open", anchored_open)
        monkeypatch.setattr(os, "rename", anchored_rename)
        monkeypatch.setattr(os, "unlink", anchored_unlink)

    monkeypatch.setattr(applier_module.secrets, "token_hex", lambda _size: "anchored-delete")

    def verify_then_substitute(
        guarded: applier_module._ParentGuard,
        path: Path,
        expected: applier_module._StagedEntry,
        *,
        trusted: bool,
        label: str = "staged file changed",
    ) -> None:
        nonlocal substituted
        real_verify(guarded, path, expected, trusted=trusted, label=label)
        if path.name.endswith(".skillcord.quarantine") and not substituted:
            path.unlink()
            path.write_bytes(attacker_bytes)
            substituted = True

    monkeypatch.setattr(applier_module, "_verify_entry_at", verify_then_substitute)

    errors = applier_module._cleanup_entries(guard, [entry])
    if not simulated_descriptor:
        guard.close()

    assert substituted is True
    assert len(errors) == 1
    assert "refusing" in str(errors[0])
    preserved = [path for path in parent.iterdir() if path.read_bytes() == attacker_bytes]
    assert len(preserved) == 1
    assert preserved[0].name.endswith(".skillcord.quarantine")


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
    temporary.write_bytes(b"staged\n")
    guard = applier_module._ParentGuard.open(tmp_path, parent)
    entry = _staged_entry(temporary)
    real_open = os.open
    real_rename = os.rename
    renamed: list[tuple[object, object, int | None, int | None]] = []
    simulated_descriptor = guard.descriptor is None
    if simulated_descriptor:
        guard.descriptor = 12345

        def resolved(path: object, dir_fd: int | None) -> Path:
            candidate = Path(os.fspath(path))
            return parent / candidate if dir_fd == guard.descriptor else candidate

        def anchored_open(
            path: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            return real_open(resolved(path, dir_fd), flags, mode)

        def anchored_rename(
            source: object,
            destination: object,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            renamed.append((source, destination, src_dir_fd, dst_dir_fd))
            real_rename(resolved(source, src_dir_fd), resolved(destination, dst_dir_fd))

        monkeypatch.setattr(os, "open", anchored_open)
        monkeypatch.setattr(os, "rename", anchored_rename)

    def fail_path_verification() -> None:
        raise StaleSyncPlanError("parent directory changed")

    monkeypatch.setattr(guard, "verify", fail_path_verification)
    monkeypatch.setattr(applier_module.secrets, "token_hex", lambda _size: "anchored")

    errors = applier_module._cleanup_entries(guard, [entry])

    if simulated_descriptor:
        assert renamed == [
            (
                temporary.name,
                f".{temporary.name}.anchored.skillcord.quarantine",
                12345,
                12345,
            )
        ]
    else:
        guard.close()
    assert errors == []
    assert list(parent.iterdir()) == []


def test_anchored_cleanup_preserves_substitution_moved_to_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "nested"
    parent.mkdir()
    temporary = parent / ".generated.secret.skillcord.tmp"
    temporary.write_bytes(b"staged\n")
    entry = _staged_entry(temporary)
    guard = applier_module._ParentGuard.open(tmp_path, parent)
    real_open = os.open
    real_rename_noreplace = applier_module._atomic_rename_noreplace
    real_rename = os.rename
    attacker_bytes = b"anchored substitute\n"
    substitution_happened = False
    simulated_descriptor = guard.descriptor is None
    if simulated_descriptor:
        guard.descriptor = 12345

    def resolved(path: object, dir_fd: int | None) -> Path:
        candidate = Path(os.fspath(path))
        return parent / candidate if dir_fd == guard.descriptor else candidate

    def anchored_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        return real_open(resolved(path, dir_fd), flags, mode)

    def substitute_then_rename(
        guarded: applier_module._ParentGuard,
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal substitution_happened
        source.unlink()
        source.write_bytes(attacker_bytes)
        substitution_happened = True
        if simulated_descriptor:
            real_rename(source, destination)
        else:
            real_rename_noreplace(guarded, source, destination)

    def fail_path_verification() -> None:
        raise StaleSyncPlanError("parent directory changed")

    monkeypatch.setattr(guard, "verify", fail_path_verification)
    monkeypatch.setattr(os, "open", anchored_open)
    monkeypatch.setattr(applier_module, "_atomic_rename_noreplace", substitute_then_rename)
    monkeypatch.setattr(applier_module.secrets, "token_hex", lambda _size: "anchored")

    errors = applier_module._cleanup_entries(guard, [entry])
    if not simulated_descriptor:
        guard.close()

    assert substitution_happened is True
    assert len(errors) == 1
    assert "staged file changed; refusing cleanup" in str(errors[0])
    preserved = [path for path in parent.iterdir() if path.read_bytes() == attacker_bytes]
    assert len(preserved) == 1
    assert preserved[0].name.endswith(".skillcord.quarantine")


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
    real_fchmod = os.fchmod

    def record_fchmod(descriptor: int, mode: int) -> None:
        descriptor_modes.append(mode)
        real_fchmod(descriptor, mode)

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
    def fail_fsync(_descriptor: int) -> None:
        raise OSError("originating staging failure")

    def fail_temp_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("secondary cleanup failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(applier_module, "_delete_verified_quarantine", fail_temp_cleanup)

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
