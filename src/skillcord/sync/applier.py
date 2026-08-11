"""Approval-gated, failure-atomic application of precomputed sync plans.

On platforms exposing directory-relative ``os.replace``/``os.unlink``, each
replacement is anchored to an already-open parent directory descriptor.  The
portable fallback verifies every parent directory's identity immediately
before and after pathname operations.  No portable userspace technique can
eliminate a malicious kernel-level race inside one fallback system call; a
detected late swap is treated as an apply failure and rollback is attempted.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


@dataclass
class _StagedChange:
    change: PlannedFileChange
    guard: _ParentGuard
    replacement: Path
    backup: Path | None
    replacement_consumed: bool = False
    backup_consumed: bool = False


@dataclass
class _CreatedDirectory:
    path: Path
    identity: _PathIdentity | None
    parent_guard: _ParentGuard


class SyncApplier:
    """Apply only frozen plan bytes; never discover or resolve providers."""

    def apply(self, plan: SyncPlan, approved: bool) -> ApplyResult:
        """Apply a plan transactionally, rolling back earlier files on failure."""

        if not approved:
            return ApplyResult(applied=False, changed_files=())

        self._preflight(plan)
        root = plan.project_root.resolve(strict=True)
        created_directories: list[_CreatedDirectory] = []
        staged: list[_StagedChange] = []
        try:
            for change in plan.changes:
                _ensure_parent_directories(
                    root,
                    change.path.parent,
                    created_directories,
                    self._before_mkdir,
                    self._after_mkdir,
                )
                guard = _ParentGuard.open(root, change.path.parent)
                try:
                    existing_mode = guard.current_mode(change.path)
                    self._before_stage(change.path)
                    guard.verify()
                    replacement = _stage_bytes(
                        guard,
                        change.path,
                        change.after,
                        existing_mode,
                        suffix=".skillcord.tmp",
                    )
                    self._after_stage(change.path)
                    backup: Path | None = None
                    try:
                        if change.before is not None:
                            backup = _stage_bytes(
                                guard,
                                change.path,
                                change.before,
                                existing_mode,
                                suffix=".skillcord.backup",
                            )
                    except BaseException as error:
                        _add_error_notes(error, "cleanup", _cleanup_paths(guard, [replacement]))
                        raise
                except BaseException as error:
                    _add_error_notes(error, "cleanup", _close_guard(guard))
                    raise
                staged.append(
                    _StagedChange(
                        change=change,
                        guard=guard,
                        replacement=replacement,
                        backup=backup,
                    )
                )

            self._validate_staged(plan, staged)
        except BaseException as error:
            cleanup_errors = _cleanup_staged(staged)
            cleanup_errors.extend(_close_guards(staged))
            cleanup_errors.extend(
                _cleanup_directories(created_directories, self._before_rmdir)
            )
            _add_error_notes(error, "cleanup", cleanup_errors)
            raise

        applied: list[_StagedChange] = []
        try:
            for item in staged:
                self._before_replace(item.change.path)
                self._validate_staged_change(item)
                applied.append(item)
                item.guard.replace(item.replacement, item.change.path)
                item.replacement_consumed = True
        except BaseException as error:
            rollback_errors = self._rollback(applied)
            cleanup_errors = _cleanup_staged(staged)
            cleanup_errors.extend(_close_guards(staged))
            cleanup_errors.extend(
                _cleanup_directories(created_directories, self._before_rmdir)
            )
            if rollback_errors:
                raise SyncRollbackError(error, rollback_errors, cleanup_errors) from error
            _add_error_notes(error, "cleanup", cleanup_errors)
            raise

        cleanup_errors = _cleanup_staged(staged)
        cleanup_errors.extend(_close_guards(staged))
        cleanup_errors.extend(_close_created_guards(created_directories))
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
    def _before_mkdir(_directory: Path) -> None:
        """Checkpoint before creating a missing target parent."""

    @staticmethod
    def _before_rmdir(_directory: Path) -> None:
        """Checkpoint before removing a directory created by this apply."""

    @staticmethod
    def _after_mkdir(_directory: Path) -> None:
        """Checkpoint after recording ownership of a newly created directory."""

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
                current = item.guard.current_bytes(item.change.path)
                if current == item.change.before:
                    continue
                if current != item.change.after:
                    raise StaleSyncPlanError(
                        f"cannot safely roll back concurrently changed file: "
                        f"{item.change.relative_path.as_posix()}"
                    )
                if item.change.before is None:
                    item.guard.unlink(item.change.path, missing_ok=False)
                else:
                    if item.backup is None:
                        raise RuntimeError("missing rollback backup for existing file")
                    item.guard.replace(item.backup, item.change.path)
                    item.backup_consumed = True
            except (OSError, RuntimeError) as error:
                errors.append(error)
        return errors


def _directory_relative_operations_supported() -> bool:
    return (
        os.rename in os.supports_dir_fd
        and os.open in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.rmdir in os.supports_dir_fd
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


def _ensure_parent_directories(
    root: Path,
    parent: Path,
    created: list[_CreatedDirectory],
    before_mkdir: Callable[[Path], None],
    after_mkdir: Callable[[Path], None],
) -> None:
    relative = parent.relative_to(root)
    if _directory_relative_operations_supported():
        _ensure_parent_directories_anchored(
            root,
            relative,
            created,
            before_mkdir,
            after_mkdir,
        )
        return

    current = root
    for part in relative.parts:
        current /= part
        if not current.exists():
            parent_identity = _PathIdentity.capture(current.parent)
            parent_guard = _ParentGuard.open(root, current.parent)
            try:
                before_mkdir(current)
                parent_identity.verify()
                current.mkdir()
            except BaseException as error:
                _add_error_notes(error, "cleanup", _close_guard(parent_guard))
                raise
            created_item = _begin_created_directory(current, parent_guard, created)
            created_item.identity = _PathIdentity.capture(current)
            after_mkdir(current)
            parent_identity.verify()
        _PathIdentity.capture(current)


def _ensure_parent_directories_anchored(
    root: Path,
    relative: Path,
    created: list[_CreatedDirectory],
    before_mkdir: Callable[[Path], None],
    after_mkdir: Callable[[Path], None],
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_path = root
    expected = _PathIdentity.capture(root)
    current_descriptor = os.open(root, flags)
    try:
        _verify_open_directory(current_descriptor, expected)
        for part in relative.parts:
            child_path = current_path / part
            child_created = False
            try:
                child_descriptor = os.open(part, flags, dir_fd=current_descriptor)
            except FileNotFoundError:
                before_mkdir(child_path)
                expected.verify()
                _verify_open_directory(current_descriptor, expected)
                parent_guard = _ParentGuard.open(root, current_path)
                try:
                    os.mkdir(part, dir_fd=current_descriptor)
                except BaseException as error:
                    _add_error_notes(error, "cleanup", _close_guard(parent_guard))
                    raise
                created_item = _begin_created_directory(child_path, parent_guard, created)
                child_descriptor = os.open(part, flags, dir_fd=current_descriptor)
                child_created = True
            child_metadata = os.fstat(child_descriptor)
            if not stat.S_ISDIR(child_metadata.st_mode):
                os.close(child_descriptor)
                raise StaleSyncPlanError(f"parent path is not a directory: {child_path}")
            if child_created:
                created_item.identity = _identity_from_metadata(child_path, child_metadata)
                after_mkdir(child_path)
            os.close(current_descriptor)
            current_descriptor = child_descriptor
            current_path = child_path
            expected = _PathIdentity.capture(current_path)
            _verify_open_directory(current_descriptor, expected)
    finally:
        os.close(current_descriptor)


def _begin_created_directory(
    directory: Path,
    parent_guard: _ParentGuard,
    created: list[_CreatedDirectory],
) -> _CreatedDirectory:
    item = _CreatedDirectory(
        path=directory,
        identity=None,
        parent_guard=parent_guard,
    )
    created.append(item)
    return item


def _identity_from_metadata(path: Path, metadata: os.stat_result) -> _PathIdentity:
    return _PathIdentity(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        file_type=stat.S_IFMT(metadata.st_mode),
    )


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
) -> Path:
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
    try:
        with os.fdopen(descriptor, "wb") as stream:
            guard.verify()
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            if mode is not None and guard.descriptor is not None:
                descriptor_chmod = getattr(os, "fchmod", None)
                if descriptor_chmod is None:
                    raise RuntimeError("descriptor chmod unavailable for anchored staging")
                descriptor_chmod(stream.fileno(), mode)
        if mode is not None and guard.descriptor is None:
            os.chmod(temporary, mode)
        guard.verify()
        return temporary
    except BaseException as error:
        _add_error_notes(error, "cleanup", _cleanup_paths(guard, [temporary]))
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


def _cleanup_paths(guard: _ParentGuard, paths: list[Path]) -> list[BaseException]:
    errors: list[BaseException] = []
    for path in paths:
        try:
            guard.unlink(path, missing_ok=True)
        except (OSError, StaleSyncPlanError) as error:
            errors.append(error)
    return errors


def _cleanup_staged(staged: list[_StagedChange]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in staged:
        paths: list[Path] = []
        if not item.replacement_consumed:
            paths.append(item.replacement)
        if item.backup is not None and not item.backup_consumed:
            paths.append(item.backup)
        errors.extend(_cleanup_paths(item.guard, paths))
    return errors


def _close_guard(guard: _ParentGuard) -> list[BaseException]:
    try:
        guard.close()
    except OSError as error:
        return [error]
    return []


def _close_guards(staged: list[_StagedChange]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in staged:
        errors.extend(_close_guard(item.guard))
    return errors


def _close_created_guards(created: list[_CreatedDirectory]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in created:
        errors.extend(_close_guard(item.parent_guard))
    return errors


def _cleanup_directories(
    created: list[_CreatedDirectory],
    before_rmdir: Callable[[Path], None],
) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in reversed(created):
        try:
            before_rmdir(item.path)
            if item.identity is None:
                raise StaleSyncPlanError(
                    f"created directory identity was not captured; refusing cleanup: {item.path}"
                )
            item.identity.verify()
            item.parent_guard.verify()
            if item.parent_guard.descriptor is not None:
                os.rmdir(item.path.name, dir_fd=item.parent_guard.descriptor)
            else:
                item.path.rmdir()
            item.parent_guard.verify()
        except FileNotFoundError:
            pass
        except (OSError, StaleSyncPlanError) as error:
            errors.append(error)
        errors.extend(_close_guard(item.parent_guard))
    return errors


def _add_error_notes(
    original: BaseException,
    label: str,
    secondary_errors: list[BaseException],
) -> None:
    for error in secondary_errors:
        original.add_note(f"Skillcord {label} error: {error}")
