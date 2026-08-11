"""Approval-gated, failure-atomic application of precomputed sync plans.

On platforms exposing directory-relative ``os.replace``/``os.unlink``, each
replacement is anchored to an already-open parent directory descriptor.  The
portable fallback verifies every parent directory's identity immediately
before and after pathname operations.  No portable userspace technique can
eliminate a malicious kernel-level race inside one fallback system call; a
detected late swap is treated as an apply failure and rollback is attempted.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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

    def current_bytes(self, target: Path) -> bytes | None:
        self.verify()
        if self.descriptor is not None:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(target.name, flags, dir_fd=self.descriptor)
            except FileNotFoundError:
                return None
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise StaleSyncPlanError(f"sync target is not a regular file: {target}")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    content = stream.read()
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            self.verify()
            return content

        if target.exists() and not target.is_file():
            raise StaleSyncPlanError(f"sync target is not a regular file: {target}")
        fallback_content = target.read_bytes() if target.exists() else None
        self.verify()
        return fallback_content

    def current_mode(self, target: Path) -> int | None:
        if self.current_bytes(target) is None:
            return None
        return stat.S_IMODE(target.stat().st_mode)

    def replace(self, source: Path, target: Path) -> None:
        self.verify()
        if self.descriptor is not None:
            os.replace(
                source.name,
                target.name,
                src_dir_fd=self.descriptor,
                dst_dir_fd=self.descriptor,
            )
        else:
            os.replace(source, target)
        self.verify()

    def unlink(self, target: Path, *, missing_ok: bool) -> None:
        self.verify()
        try:
            if self.descriptor is not None:
                os.unlink(target.name, dir_fd=self.descriptor)
            else:
                target.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise
        self.verify()

    def unlink_staged(self, target: Path, *, missing_ok: bool) -> None:
        """Remove a staged name through its retained trusted directory handle."""

        if self.descriptor is None:
            self.unlink(target, missing_ok=missing_ok)
            return
        try:
            os.unlink(target.name, dir_fd=self.descriptor)
        except FileNotFoundError:
            if not missing_ok:
                raise

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
    replacement_consumed: bool = False
    backup_consumed: bool = False


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


class SyncApplier:
    """Apply only frozen plan bytes; never discover or resolve providers."""

    def apply(self, plan: SyncPlan, approved: bool) -> ApplyResult:
        """Apply a plan transactionally, rolling back earlier files on failure."""

        if not approved:
            return ApplyResult(applied=False, changed_files=())

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
                    existing_mode = guard.current_mode(change.path)
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
        current = item.guard.current_bytes(item.change.path)
        if current != item.change.before:
            raise StaleSyncPlanError(
                f"file changed after preview: {item.change.relative_path.as_posix()}"
            )

    @staticmethod
    def _rollback(applied: list[_StagedChange]) -> list[BaseException]:
        errors: list[BaseException] = []
        for item in reversed(applied):
            try:
                trusted_parent = item.guard.descriptor is not None
                current = _current_bytes(
                    item.guard,
                    item.change.path,
                    trusted=trusted_parent,
                )
                if current == item.change.before:
                    continue
                if current != item.change.after:
                    raise StaleSyncPlanError(
                        f"cannot safely roll back concurrently changed file: "
                        f"{item.change.relative_path.as_posix()}"
                    )
                if item.change.before is None:
                    _verify_entry_at(
                        item.guard,
                        item.change.path,
                        item.replacement,
                        trusted=trusted_parent,
                        label="installed file changed; refusing rollback",
                    )
                    if trusted_parent:
                        item.guard.unlink_staged(item.change.path, missing_ok=False)
                    else:
                        item.guard.unlink(item.change.path, missing_ok=False)
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
                errors.append(error)
        return errors


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


def _verify_open_directory(descriptor: int, expected: _PathIdentity) -> None:
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
        expected.device,
        expected.inode,
        expected.file_type,
    ):
        raise StaleSyncPlanError(f"parent directory changed: {expected.path}")


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
    created_identity = _FileIdentity.from_stat(os.fstat(descriptor))
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
                after_write(temporary)
                guard.verify()
                metadata = os.fstat(stream.fileno())
                entry = _StagedEntry(
                    path=temporary,
                    identity=_FileIdentity.from_stat(metadata),
                    size=metadata.st_size,
                    mode=stat.S_IMODE(metadata.st_mode),
                    content_hash=hashlib.sha256(content).digest(),
                )
            except BaseException as error:
                _add_error_notes(error, "payload scrub", _truncate_open_stream(stream))
                raise
        return entry
    except BaseException as error:
        _add_error_notes(
            error,
            "cleanup",
            _cleanup_owned_path(guard, temporary, created_identity),
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
        if path_metadata is not None and _FileIdentity.from_stat(path_metadata) != (
            _FileIdentity.from_stat(metadata)
        ):
            raise StaleSyncPlanError(f"staged file changed: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content_hash = hashlib.sha256(stream.read()).digest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not trusted:
        guard.verify()
    return (
        _FileIdentity.from_stat(metadata),
        metadata.st_size,
        stat.S_IMODE(metadata.st_mode),
        content_hash,
    )


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


def _current_bytes(guard: _ParentGuard, path: Path, *, trusted: bool) -> bytes | None:
    if not trusted:
        return guard.current_bytes(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.name, flags, dir_fd=guard.descriptor)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StaleSyncPlanError(f"sync target is not a regular file: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _cleanup_owned_path(
    guard: _ParentGuard,
    path: Path,
    identity: _FileIdentity,
) -> list[BaseException]:
    errors: list[BaseException] = []
    trusted = guard.descriptor is not None
    try:
        observed_identity, _size, _mode, _hash = _observe_file(
            guard,
            path,
            trusted=trusted,
        )
        if observed_identity != identity:
            raise StaleSyncPlanError(f"staged file changed; refusing cleanup: {path}")
        guard.unlink_staged(path, missing_ok=True)
    except FileNotFoundError:
        pass
    except (OSError, StaleSyncPlanError) as error:
        errors.append(error)
    return errors


def _cleanup_entries(
    guard: _ParentGuard,
    entries: list[_StagedEntry],
) -> list[BaseException]:
    errors: list[BaseException] = []
    trusted = guard.descriptor is not None
    for entry in entries:
        try:
            _verify_entry_at(guard, entry.path, entry, trusted=trusted)
            guard.unlink_staged(entry.path, missing_ok=True)
        except FileNotFoundError:
            pass
        except (OSError, StaleSyncPlanError) as error:
            if isinstance(error.__cause__, FileNotFoundError):
                continue
            errors.append(error)
    return errors


def _cleanup_staged(staged: list[_StagedChange]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in staged:
        entries: list[_StagedEntry] = []
        if not item.replacement_consumed:
            entries.append(item.replacement)
        if item.backup is not None and not item.backup_consumed:
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
