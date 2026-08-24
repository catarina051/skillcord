"""Approval-gated, failure-atomic application of precomputed sync plans.

Replacement is anchored to an already-open parent directory descriptor when
the platform supports it. Cleanup first moves an entry with a platform-native
atomic no-replace operation. Windows then deletes the verified file by handle.
POSIX reopens and revalidates the directory entry after hashing, rechecks it
immediately before an anchored unlink, and documents the irreducible same-UID
race between those two syscalls as outside the V1 threat model.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib
import os
import platform
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

from skillcord.history.store import HistoryEntry, HistoryStore, ProviderRevision
from skillcord.sync.planner import PlannedFileChange, SyncPlan, resolve_target_within_root


class StaleSyncPlanError(RuntimeError):
    """Raised when files or their parent chain changed after preview."""


class SyncRollbackError(RuntimeError):
    """Report both an apply failure and failures restoring the prior state."""

    def __init__(
        self,
        original_error: BaseException,
        rollback_errors: list[BaseException],
        cleanup_errors: list[BaseException] | None = None,
    ) -> None:
        self.original_error = original_error
        self.rollback_errors = tuple(rollback_errors)
        self.cleanup_errors = tuple(cleanup_errors or ())
        super().__init__(
            f"sync apply failed ({original_error}); rollback also failed: "
            + "; ".join(str(error) for error in rollback_errors)
        )


class SyncAuditError(RuntimeError):
    """Raised when history recording fails during the guarded sync commit."""

    def __init__(self, original_error: Exception) -> None:
        self.original_error = original_error
        super().__init__(f"sync audit failed while committing history: {original_error}")


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of an approval decision for a precomputed plan."""

    applied: bool
    changed_files: tuple[Path, ...]
    cleanup_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PathIdentity:
    path: Path
    device: int
    inode: int
    file_type: int

    @classmethod
    def capture(cls, path: Path) -> _PathIdentity:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise StaleSyncPlanError(f"parent path is not a real directory: {path}")
        return cls(
            path=path,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            file_type=stat.S_IFMT(metadata.st_mode),
        )

    def verify(self) -> None:
        try:
            current = _PathIdentity.capture(self.path)
        except (FileNotFoundError, OSError) as error:
            raise StaleSyncPlanError(f"parent directory changed: {self.path}") from error
        if current != self:
            raise StaleSyncPlanError(f"parent directory changed: {self.path}")


@dataclass
class _ParentGuard:
    root: Path
    parent: Path
    identities: tuple[_PathIdentity, ...]
    descriptor: int | None = None

    @classmethod
    def open(cls, root: Path, parent: Path) -> _ParentGuard:
        identities = _capture_parent_chain(root, parent)
        descriptor: int | None = None
        if _directory_relative_operations_supported():
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(parent, flags)
            opened = os.fstat(descriptor)
            expected = identities[-1]
            if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
                expected.device,
                expected.inode,
                expected.file_type,
            ):
                os.close(descriptor)
                raise StaleSyncPlanError(f"parent directory changed: {parent}")
        return cls(root=root, parent=parent, identities=identities, descriptor=descriptor)

    def verify(self) -> None:
        for identity in self.identities:
            identity.verify()
        try:
            resolved = self.parent.resolve(strict=True)
            resolved.relative_to(self.root)
        except (FileNotFoundError, OSError, ValueError) as error:
            raise StaleSyncPlanError(f"parent directory changed: {self.parent}") from error
        if resolved != self.parent:
            raise StaleSyncPlanError(f"parent directory changed: {self.parent}")
        if self.descriptor is not None:
            opened = os.fstat(self.descriptor)
            expected = self.identities[-1]
            if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
                expected.device,
                expected.inode,
                expected.file_type,
            ):
                raise StaleSyncPlanError(f"parent directory changed: {self.parent}")

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


@dataclass
class _StagedChange:
    change: PlannedFileChange
    guard: _ParentGuard
    staging_guard: _ParentGuard
    replacement: _StagedEntry
    backup: _StagedEntry | None
    original: _StagedEntry | None
    replacement_consumed: bool = False
    backup_consumed: bool = False
    preserve_backup: bool = False


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    file_type: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> _FileIdentity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            file_type=stat.S_IFMT(metadata.st_mode),
        )


@dataclass(frozen=True)
class _StagedEntry:
    path: Path
    identity: _FileIdentity
    size: int
    mode: int
    content_hash: bytes


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SyncApplier:
    """Apply only frozen plan bytes; never discover or resolve providers."""

    def __init__(
        self,
        history_store: HistoryStore | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        provider_revisions: Mapping[str, Mapping[str, str | None]] | None = None,
    ) -> None:
        self._history_store = history_store
        self._clock = clock or _utc_now
        self._provider_revisions = provider_revisions or {}

    def apply(self, plan: SyncPlan, approved: bool) -> ApplyResult:
        """Apply a plan transactionally, rolling back earlier files on failure."""

        if not approved:
            return ApplyResult(applied=False, changed_files=())

        history_entry = self._prepare_history_entry(plan)
        if history_entry is not None and self._history_store is not None:
            self._history_store.list()
        self._preflight(plan)
        root = plan.project_root.resolve(strict=True)
        staged: list[_StagedChange] = []
        try:
            for change in plan.changes:
                guard = _ParentGuard.open(root, change.path.parent)
                staging_guard = guard
                try:
                    if guard.descriptor is None and guard.parent != root:
                        staging_guard = _ParentGuard.open(root, root)
                    original = _capture_original_entry(guard, change)
                    existing_mode = original.mode if original is not None else None
                    self._before_stage(change.path)
                    guard.verify()
                    replacement = _stage_bytes(
                        staging_guard,
                        change.path,
                        change.after,
                        existing_mode,
                        suffix=".skillcord.tmp",
                        after_write=self._after_stage_write,
                    )
                    backup: _StagedEntry | None = None
                    try:
                        self._after_stage(change.path)
                        if change.before is not None:
                            backup = _stage_bytes(
                                staging_guard,
                                change.path,
                                change.before,
                                existing_mode,
                                suffix=".skillcord.backup",
                                after_write=self._after_stage_write,
                            )
                    except BaseException as error:
                        _add_error_notes(
                            error,
                            "cleanup",
                            _cleanup_entries(staging_guard, [replacement]),
                        )
                        raise
                except BaseException as error:
                    _add_error_notes(
                        error,
                        "cleanup",
                        _close_unique_guards((guard, staging_guard)),
                    )
                    raise
                staged.append(
                    _StagedChange(
                        change=change,
                        guard=guard,
                        staging_guard=staging_guard,
                        replacement=replacement,
                        backup=backup,
                        original=original,
                    )
                )

            self._validate_staged(plan, staged)
        except BaseException as error:
            cleanup_errors = _cleanup_staged(staged)
            cleanup_errors.extend(_close_guards(staged))
            _add_error_notes(error, "cleanup", cleanup_errors)
            raise

        applied: list[_StagedChange] = []
        try:
            for item in staged:
                self._before_replace(item.change.path)
                self._validate_staged_change(item)
                applied.append(item)
                _replace_staged(item, item.replacement, item.change.path)
                item.replacement_consumed = True
            if history_entry is not None and self._history_store is not None:
                try:
                    self._history_store.append(history_entry)
                except Exception as error:
                    raise SyncAuditError(error) from error
        except BaseException as error:
            rollback_errors = self._rollback(applied)
            cleanup_errors = _cleanup_staged(staged)
            cleanup_errors.extend(_close_guards(staged))
            if rollback_errors:
                raise SyncRollbackError(error, rollback_errors, cleanup_errors) from error
            _add_error_notes(error, "cleanup", cleanup_errors)
            raise

        cleanup_errors = _cleanup_staged(staged)
        cleanup_errors.extend(_close_guards(staged))
        return ApplyResult(
            applied=True,
            changed_files=tuple(change.path for change in plan.changes),
            cleanup_errors=tuple(str(error) for error in cleanup_errors),
        )

    def _prepare_history_entry(self, plan: SyncPlan) -> HistoryEntry | None:
        if self._history_store is None or not plan.changes:
            return None
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("sync audit clock must return a timezone-aware datetime")
        rendered_timestamp = (
            timestamp.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        return HistoryEntry(
            timestamp=rendered_timestamp,
            action="sync",
            files_changed=[change.relative_path.as_posix() for change in plan.changes],
            provider_revisions={
                provider_id: ProviderRevision.model_validate(dict(revision))
                for provider_id, revision in self._provider_revisions.items()
            },
            deterministic_inverse=all(
                change.ownership == "managed_block" for change in plan.changes
            ),
        )

    @staticmethod
    def _before_replace(_target: Path) -> None:
        """Checkpoint for embedders/tests before the final guarded lookup."""

    @staticmethod
    def _before_stage(_target: Path) -> None:
        """Checkpoint before staging under a verified parent."""

    @staticmethod
    def _after_stage(_target: Path) -> None:
        """Checkpoint after staging and before the final stale-state validation."""

    @staticmethod
    def _after_stage_write(_temporary: Path) -> None:
        """Checkpoint while the staged payload handle remains open."""

    @staticmethod
    def _preflight(plan: SyncPlan) -> None:
        root = plan.project_root.resolve(strict=True)
        for change in plan.changes:
            SyncApplier._preflight_change(root, change)

    @staticmethod
    def _preflight_change(root: Path, change: PlannedFileChange) -> None:
        target, _relative = resolve_target_within_root(root, change.path)
        if target != change.path:
            raise StaleSyncPlanError(f"target path changed after preview: {change.relative_path}")
        if not target.parent.is_dir():
            raise StaleSyncPlanError(
                f"target parent does not exist: {change.relative_path.parent.as_posix()}"
            )
        if target.exists() and not target.is_file():
            raise StaleSyncPlanError(
                f"file changed after preview: {change.relative_path.as_posix()}"
            )
        current = target.read_bytes() if target.exists() and target.is_file() else None
        if current != change.before:
            raise StaleSyncPlanError(
                f"file changed after preview: {change.relative_path.as_posix()}"
            )

    @staticmethod
    def _validate_staged(plan: SyncPlan, staged: list[_StagedChange]) -> None:
        SyncApplier._preflight(plan)
        for item in staged:
            SyncApplier._validate_staged_change(item)

    @staticmethod
    def _validate_staged_change(item: _StagedChange) -> None:
        item.staging_guard.verify()
        _verify_entry_at(
            item.staging_guard,
            item.replacement.path,
            item.replacement,
            trusted=False,
        )
        if item.backup is not None:
            _verify_entry_at(
                item.staging_guard,
                item.backup.path,
                item.backup,
                trusted=False,
            )
        current = _entry_at(item.guard, item.change.path, trusted=False)
        if not _same_entry(current, item.original):
            raise StaleSyncPlanError(
                f"file changed after preview: {item.change.relative_path.as_posix()}"
            )

    @staticmethod
    def _rollback(applied: list[_StagedChange]) -> list[BaseException]:
        errors: list[BaseException] = []
        for item in reversed(applied):
            try:
                trusted_parent = item.guard.descriptor is not None
                current = _entry_at(
                    item.guard,
                    item.change.path,
                    trusted=trusted_parent,
                )
                if _same_entry(current, item.original):
                    continue
                if current is None or not _same_entry(current, item.replacement):
                    raise StaleSyncPlanError(
                        "installed file changed; refusing rollback: "
                        f"{item.change.relative_path.as_posix()}"
                    )
                if item.change.before is None:
                    try:
                        _quarantine_and_delete(
                            item.guard,
                            item.change.path,
                            item.replacement,
                            trusted=trusted_parent,
                            label="installed file changed; refusing rollback",
                        )
                    except FileNotFoundError:
                        if _entry_at(
                            item.guard,
                            item.change.path,
                            trusted=trusted_parent,
                        ) is not None:
                            raise StaleSyncPlanError(
                                "installed file changed; refusing rollback: "
                                f"{item.change.relative_path.as_posix()}"
                            ) from None
                else:
                    if item.backup is None:
                        raise RuntimeError("missing rollback backup for existing file")
                    _replace_staged(
                        item,
                        item.backup,
                        item.change.path,
                        trusted_parent=trusted_parent,
                        expected_target=item.replacement,
                    )
                    item.backup_consumed = True
            except (OSError, RuntimeError) as error:
                if item.backup is not None and not item.backup_consumed:
                    item.preserve_backup = True
                errors.append(error)
        return errors


def _capture_original_entry(
    guard: _ParentGuard,
    change: PlannedFileChange,
) -> _StagedEntry | None:
    original = _entry_at(guard, change.path, trusted=False)
    if change.before is None:
        if original is not None:
            raise StaleSyncPlanError(
                f"file changed after preview: {change.relative_path.as_posix()}"
            )
        return None
    if (
        original is None
        or original.size != len(change.before)
        or original.content_hash != hashlib.sha256(change.before).digest()
    ):
        raise StaleSyncPlanError(
            f"file changed after preview: {change.relative_path.as_posix()}"
        )
    return original


def _directory_relative_operations_supported() -> bool:
    return (
        os.rename in os.supports_dir_fd
        and os.open in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "fchmod")
    )


def _capture_parent_chain(root: Path, parent: Path) -> tuple[_PathIdentity, ...]:
    try:
        relative = parent.relative_to(root)
    except ValueError as error:
        raise StaleSyncPlanError(f"parent directory escapes project root: {parent}") from error
    chain = [root]
    current = root
    for part in relative.parts:
        current /= part
        chain.append(current)
    return tuple(_PathIdentity.capture(path) for path in chain)


def _stage_bytes(
    guard: _ParentGuard,
    target: Path,
    content: bytes,
    mode: int | None,
    *,
    suffix: str,
    after_write: Callable[[Path], None],
) -> _StagedEntry:
    guard.verify()
    if guard.descriptor is not None:
        descriptor, temporary = _open_anchored_temporary(guard, target, suffix)
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=suffix,
            dir=guard.parent,
        )
        temporary = Path(temporary_name)
    created_metadata = os.fstat(descriptor)
    created_identity = _FileIdentity.from_stat(created_metadata)
    expected_mode = stat.S_IMODE(created_metadata.st_mode)
    cleanup_entry = _StagedEntry(
        path=temporary,
        identity=created_identity,
        size=0,
        mode=expected_mode,
        content_hash=hashlib.sha256(b"").digest(),
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            try:
                guard.verify()
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                if mode is not None:
                    descriptor_chmod = getattr(os, "fchmod", None)
                    if descriptor_chmod is None:
                        raise RuntimeError("descriptor chmod unavailable for safe staging")
                    descriptor_chmod(stream.fileno(), mode)
                    expected_mode = mode
                after_write(temporary)
                guard.verify()
                metadata = os.fstat(stream.fileno())
                if stat.S_IMODE(metadata.st_mode) != expected_mode:
                    raise StaleSyncPlanError(f"staged file mode changed: {temporary}")
                if (
                    _FileIdentity.from_stat(metadata) != created_identity
                    or metadata.st_size != len(content)
                ):
                    raise StaleSyncPlanError(f"staged file changed: {temporary}")
                entry = _StagedEntry(
                    path=temporary,
                    identity=created_identity,
                    size=len(content),
                    mode=expected_mode,
                    content_hash=hashlib.sha256(content).digest(),
                )
            except BaseException as error:
                scrub_errors = _truncate_open_stream(stream)
                cleanup_entry = _StagedEntry(
                    path=temporary,
                    identity=created_identity,
                    size=0,
                    mode=expected_mode,
                    content_hash=hashlib.sha256(b"").digest(),
                )
                _add_error_notes(error, "payload scrub", scrub_errors)
                raise
        return entry
    except BaseException as error:
        _add_error_notes(
            error,
            "cleanup",
            _cleanup_owned_path(guard, cleanup_entry),
        )
        raise


def _open_anchored_temporary(
    guard: _ParentGuard,
    target: Path,
    suffix: str,
) -> tuple[int, Path]:
    if guard.descriptor is None:
        raise RuntimeError("anchored temporary requires a directory descriptor")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _attempt in range(100):
        name = f".{target.name}.{secrets.token_hex(8)}{suffix}"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=guard.descriptor)
        except FileExistsError:
            continue
        return descriptor, guard.parent / name
    raise FileExistsError("could not allocate a unique Skillcord temporary file")


def _observe_file(
    guard: _ParentGuard,
    path: Path,
    *,
    trusted: bool,
) -> tuple[_FileIdentity, int, int, bytes]:
    if not trusted:
        guard.verify()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    path_metadata: os.stat_result | None = None
    if guard.descriptor is not None:
        descriptor = os.open(path.name, flags, dir_fd=guard.descriptor)
    else:
        path_metadata = path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode):
            raise StaleSyncPlanError(f"staged file changed: {path}")
        descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StaleSyncPlanError(f"staged file changed: {path}")
        opened_values = _metadata_values(metadata)
        if path_metadata is not None and _metadata_values(path_metadata) != opened_values:
            raise StaleSyncPlanError(f"staged file changed: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content_hash = hashlib.sha256(stream.read()).digest()
            if _metadata_values(os.fstat(stream.fileno())) != opened_values:
                raise StaleSyncPlanError(f"staged file changed: {path}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if _reopen_entry_metadata(guard, path) != opened_values:
        raise StaleSyncPlanError(f"staged file changed: {path}")
    if not trusted:
        guard.verify()
    return (
        opened_values[0],
        opened_values[1],
        opened_values[2],
        content_hash,
    )


def _metadata_values(metadata: os.stat_result) -> tuple[_FileIdentity, int, int]:
    return (
        _FileIdentity.from_stat(metadata),
        metadata.st_size,
        stat.S_IMODE(metadata.st_mode),
    )


def _reopen_entry_metadata(
    guard: _ParentGuard,
    path: Path,
) -> tuple[_FileIdentity, int, int]:
    """Re-open the directory entry no-follow after hashing and revalidate it."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    path_metadata: os.stat_result | None = None
    if guard.descriptor is not None:
        descriptor = os.open(path.name, flags, dir_fd=guard.descriptor)
    else:
        path_metadata = path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode):
            raise StaleSyncPlanError(f"staged file changed: {path}")
        descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        values = _metadata_values(metadata)
        if not stat.S_ISREG(metadata.st_mode):
            raise StaleSyncPlanError(f"staged file changed: {path}")
        if path_metadata is not None and _metadata_values(path_metadata) != values:
            raise StaleSyncPlanError(f"staged file changed: {path}")
        return values
    finally:
        os.close(descriptor)


def _verify_entry_at(
    guard: _ParentGuard,
    path: Path,
    expected: _StagedEntry,
    *,
    trusted: bool,
    label: str = "staged file changed",
) -> None:
    try:
        observed = _observe_file(guard, path, trusted=trusted)
    except FileNotFoundError as error:
        raise StaleSyncPlanError(f"{label}: {path}") from error
    expected_values = (
        expected.identity,
        expected.size,
        expected.mode,
        expected.content_hash,
    )
    if observed != expected_values:
        raise StaleSyncPlanError(f"{label}: {path}")


def _entry_at(
    guard: _ParentGuard,
    path: Path,
    *,
    trusted: bool,
) -> _StagedEntry | None:
    try:
        identity, size, mode, content_hash = _observe_file(guard, path, trusted=trusted)
    except FileNotFoundError:
        return None
    return _StagedEntry(
        path=path,
        identity=identity,
        size=size,
        mode=mode,
        content_hash=content_hash,
    )


def _same_entry(left: _StagedEntry | None, right: _StagedEntry | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.identity,
        left.size,
        left.mode,
        left.content_hash,
    ) == (
        right.identity,
        right.size,
        right.mode,
        right.content_hash,
    )


def _rename_to_quarantine(
    guard: _ParentGuard,
    source: Path,
    quarantine: Path,
    *,
    trusted: bool,
) -> None:
    if not trusted:
        guard.verify()
    try:
        _atomic_rename_noreplace(guard, source, quarantine)
    except OSError as error:
        unsupported = {
            errno.EINVAL,
            errno.ENOSYS,
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }
        if error.errno in unsupported:
            raise StaleSyncPlanError(
                "atomic no-replace rename unavailable; preserved source entry: "
                f"{source}"
            ) from error
        raise
    if not trusted:
        guard.verify()


def _atomic_rename_noreplace(
    guard: _ParentGuard,
    source: Path,
    destination: Path,
) -> None:
    """Rename one entry without ever replacing an existing destination."""

    if os.name == "nt":
        if guard.descriptor is not None:
            os.rename(
                source.name,
                destination.name,
                src_dir_fd=guard.descriptor,
                dst_dir_fd=guard.descriptor,
            )
        else:
            os.rename(source, destination)
        return
    if platform.system() == "Linux":
        _linux_rename_noreplace(guard, source, destination)
        return
    if platform.system() == "Darwin":
        _macos_rename_noreplace(guard, source, destination)
        return
    raise StaleSyncPlanError(
        "atomic no-replace rename unavailable; preserved source entry: " f"{source}"
    )


def _linux_rename_noreplace(
    guard: _ParentGuard,
    source: Path,
    destination: Path,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    old_dir_fd, old_name, new_dir_fd, new_name = _renameat_arguments(
        guard,
        source,
        destination,
        at_fdcwd=-100,
    )
    renameat2: Any | None = getattr(libc, "renameat2", None)
    ctypes.set_errno(0)
    if renameat2 is not None:
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(old_dir_fd, old_name, new_dir_fd, new_name, 1)
    else:
        syscall_number = _linux_renameat2_syscall_number()
        syscall: Any | None = getattr(libc, "syscall", None)
        if syscall_number is None or syscall is None:
            raise StaleSyncPlanError(
                "atomic no-replace rename unavailable; preserved source entry: "
                f"{source}"
            )
        syscall.restype = ctypes.c_long
        result = syscall(
            ctypes.c_long(syscall_number),
            ctypes.c_int(old_dir_fd),
            ctypes.c_char_p(old_name),
            ctypes.c_int(new_dir_fd),
            ctypes.c_char_p(new_name),
            ctypes.c_uint(1),
        )
    if result != 0:
        _raise_posix_rename_error(source, destination)


def _linux_renameat2_syscall_number() -> int | None:
    machine = platform.machine().lower()
    return {
        "aarch64": 276,
        "arm64": 276,
        "x86_64": 316,
    }.get(machine)


def _macos_rename_noreplace(
    guard: _ParentGuard,
    source: Path,
    destination: Path,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename_exclusive = 0x00000004
    ctypes.set_errno(0)
    if guard.descriptor is not None:
        renameatx_np: Any | None = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise StaleSyncPlanError(
                "atomic directory-relative no-replace rename unavailable; "
                f"preserved source entry: {source}"
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            guard.descriptor,
            os.fsencode(source.name),
            guard.descriptor,
            os.fsencode(destination.name),
            rename_exclusive,
        )
    else:
        renamex_np: Any | None = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise StaleSyncPlanError(
                "atomic no-replace rename unavailable; preserved source entry: "
                f"{source}"
            )
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(
            os.fsencode(source),
            os.fsencode(destination),
            rename_exclusive,
        )
    if result != 0:
        _raise_posix_rename_error(source, destination)


def _renameat_arguments(
    guard: _ParentGuard,
    source: Path,
    destination: Path,
    *,
    at_fdcwd: int,
) -> tuple[int, bytes, int, bytes]:
    if guard.descriptor is not None:
        return (
            guard.descriptor,
            os.fsencode(source.name),
            guard.descriptor,
            os.fsencode(destination.name),
        )
    return (
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
    )


def _raise_posix_rename_error(source: Path, destination: Path) -> None:
    error_number = ctypes.get_errno() or errno.EIO
    raise OSError(
        error_number,
        os.strerror(error_number),
        os.fspath(source),
        None,
        os.fspath(destination),
    )


class _WindowsFileDispositionInfo(ctypes.Structure):
    _fields_ = [("DeleteFile", ctypes.c_ubyte)]


def _delete_verified_quarantine(
    guard: _ParentGuard,
    path: Path,
    expected: _StagedEntry,
    *,
    trusted: bool,
    label: str,
) -> None:
    if os.name == "nt":
        _windows_delete_verified_quarantine(
            guard,
            path,
            expected,
            trusted=trusted,
            label=label,
        )
        return
    if os.name != "posix":
        raise StaleSyncPlanError(
            "safe quarantine deletion unavailable; preserved verified quarantine: " f"{path}"
        )

    # POSIX has no inode-compare unlink primitive. Re-observe the name after
    # the first quarantine verification and make unlink the immediately next
    # namespace operation. A malicious same-UID writer can still race inside
    # that final syscall boundary; V1 does not claim protection from one.
    _verify_entry_at(
        guard,
        path,
        expected,
        trusted=trusted,
        label=label,
    )
    if guard.descriptor is not None:
        os.unlink(path.name, dir_fd=guard.descriptor)
        return
    if not trusted:
        guard.verify()
    path.unlink()
    if not trusted:
        guard.verify()


def _windows_delete_verified_quarantine(
    guard: _ParentGuard,
    path: Path,
    expected: _StagedEntry,
    *,
    trusted: bool,
    label: str,
) -> None:
    """Exclusively open, verify, and delete the exact Windows file by handle."""

    if not trusted:
        guard.verify()
    loader: Any | None = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise StaleSyncPlanError(
            "atomic compare-delete unavailable; preserved verified quarantine: " f"{path}"
        )
    kernel32: Any = loader("kernel32", use_last_error=True)
    create_file: Any = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle: Any = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    set_information: Any = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int

    generic_read = 0x80000000
    delete_access = 0x00010000
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    handle = create_file(
        os.fspath(path),
        generic_read | delete_access,
        0,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise _windows_error(path)

    raw_handle_owned = True
    descriptor = -1
    try:
        msvcrt = importlib.import_module("msvcrt")
        open_osfhandle = cast(Callable[[int, int], int], msvcrt.open_osfhandle)
        descriptor = open_osfhandle(
            cast(int, handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        raw_handle_owned = False
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            metadata = os.fstat(stream.fileno())
            opened_values = _metadata_values(metadata)
            if not stat.S_ISREG(metadata.st_mode):
                raise StaleSyncPlanError(f"{label}: {path}")
            path_metadata = path.lstat()
            if stat.S_ISLNK(path_metadata.st_mode):
                raise StaleSyncPlanError(f"{label}: {path}")
            if _metadata_values(path_metadata) != opened_values:
                raise StaleSyncPlanError(f"{label}: {path}")
            content_hash = hashlib.sha256(stream.read()).digest()
            if _metadata_values(os.fstat(stream.fileno())) != opened_values:
                raise StaleSyncPlanError(f"{label}: {path}")
            if _metadata_values(path.lstat()) != opened_values:
                raise StaleSyncPlanError(f"{label}: {path}")
            observed = (*opened_values, content_hash)
            expected_values = (
                expected.identity,
                expected.size,
                expected.mode,
                expected.content_hash,
            )
            if observed != expected_values:
                raise StaleSyncPlanError(f"{label}: {path}")
            if not trusted:
                guard.verify()
            disposition = _WindowsFileDispositionInfo(1)
            if not set_information(
                handle,
                4,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                raise _windows_error(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if raw_handle_owned:
            close_handle(handle)

    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise StaleSyncPlanError(f"{label}: replacement appeared after deletion: {path}")
    if not trusted:
        guard.verify()


def _windows_error(path: Path) -> OSError:
    error_number = ctypes.get_last_error()
    message = ctypes.FormatError(error_number)
    if error_number in {2, 3}:
        return FileNotFoundError(error_number, message, os.fspath(path))
    if error_number == 5:
        return PermissionError(error_number, message, os.fspath(path))
    return OSError(error_number, message, os.fspath(path))


def _quarantine_and_delete(
    guard: _ParentGuard,
    path: Path,
    expected: _StagedEntry,
    *,
    trusted: bool,
    label: str,
) -> None:
    for _attempt in range(100):
        quarantine = guard.parent / (
            f".{path.name}.{secrets.token_hex(8)}.skillcord.quarantine"
        )
        try:
            _rename_to_quarantine(
                guard,
                path,
                quarantine,
                trusted=trusted,
            )
        except FileExistsError:
            continue
        _verify_entry_at(
            guard,
            quarantine,
            expected,
            trusted=trusted,
            label=label,
        )
        _delete_verified_quarantine(
            guard,
            quarantine,
            expected,
            trusted=trusted,
            label=label,
        )
        return
    raise FileExistsError(f"could not allocate a quarantine name for: {path}")


def _cleanup_owned_path(
    guard: _ParentGuard,
    entry: _StagedEntry,
) -> list[BaseException]:
    return _cleanup_entries(guard, [entry])


def _cleanup_entries(
    guard: _ParentGuard,
    entries: list[_StagedEntry],
) -> list[BaseException]:
    errors: list[BaseException] = []
    trusted = guard.descriptor is not None
    for entry in entries:
        try:
            _quarantine_and_delete(
                guard,
                entry.path,
                entry,
                trusted=trusted,
                label="staged file changed; refusing cleanup",
            )
        except FileNotFoundError:
            pass
        except (OSError, StaleSyncPlanError) as error:
            errors.append(error)
    return errors


def _cleanup_staged(staged: list[_StagedChange]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in staged:
        entries: list[_StagedEntry] = []
        if not item.replacement_consumed:
            entries.append(item.replacement)
        if item.backup is not None and not item.backup_consumed and not item.preserve_backup:
            entries.append(item.backup)
        errors.extend(_cleanup_entries(item.staging_guard, entries))
    return errors


def _close_guard(guard: _ParentGuard) -> list[BaseException]:
    try:
        guard.close()
    except OSError as error:
        return [error]
    return []


def _close_guards(staged: list[_StagedChange]) -> list[BaseException]:
    return _close_unique_guards(
        tuple(guard for item in staged for guard in (item.guard, item.staging_guard))
    )


def _close_unique_guards(guards: tuple[_ParentGuard, ...]) -> list[BaseException]:
    errors: list[BaseException] = []
    closed: set[int] = set()
    for guard in guards:
        identity = id(guard)
        if identity in closed:
            continue
        closed.add(identity)
        errors.extend(_close_guard(guard))
    return errors


def _replace_staged(
    item: _StagedChange,
    source: _StagedEntry,
    target: Path,
    *,
    trusted_parent: bool = False,
    expected_target: _StagedEntry | None = None,
) -> None:
    trusted_staging = trusted_parent and item.staging_guard.descriptor is not None
    _verify_entry_at(
        item.staging_guard,
        source.path,
        source,
        trusted=trusted_staging,
    )
    if expected_target is not None:
        _verify_entry_at(
            item.guard,
            target,
            expected_target,
            trusted=trusted_parent,
            label="installed file changed; refusing rollback",
        )
    if not trusted_parent:
        item.guard.verify()
    if item.staging_guard is item.guard:
        if item.guard.descriptor is not None:
            os.replace(
                source.path.name,
                target.name,
                src_dir_fd=item.guard.descriptor,
                dst_dir_fd=item.guard.descriptor,
            )
        else:
            os.replace(source.path, target)
    else:
        os.replace(source.path, target)
    trusted_target = trusted_parent or item.guard.descriptor is not None
    _verify_entry_at(
        item.guard,
        target,
        source,
        trusted=trusted_target,
        label="installed file does not match approved stage",
    )
    if not trusted_parent:
        item.guard.verify()
        if item.staging_guard is not item.guard:
            item.staging_guard.verify()


def _truncate_open_stream(stream: BinaryIO) -> list[BaseException]:
    errors: list[BaseException] = []
    try:
        stream.seek(0)
        stream.truncate(0)
        stream.flush()
        os.fsync(stream.fileno())
    except (OSError, ValueError) as error:
        errors.append(error)
    return errors


def _add_error_notes(
    original: BaseException,
    label: str,
    secondary_errors: list[BaseException],
) -> None:
    for error in secondary_errors:
        original.add_note(f"Skillcord {label} error: {error}")
