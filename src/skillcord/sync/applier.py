"""Approval-gated atomic application of precomputed sync plans."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skillcord.sync.planner import PlannedFileChange, SyncPlan, resolve_target_within_root


class StaleSyncPlanError(RuntimeError):
    """Raised when files changed after their sync preview was computed."""


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of an approval decision for a precomputed plan."""

    applied: bool
    changed_files: tuple[Path, ...]


class SyncApplier:
    """Apply only frozen plan bytes; never discover or resolve providers."""

    def apply(self, plan: SyncPlan, approved: bool) -> ApplyResult:
        """Atomically replace each planned file after approval and stale checks."""

        if not approved:
            return ApplyResult(applied=False, changed_files=())

        self._preflight(plan)
        staged: list[tuple[Path, Path]] = []
        try:
            for change in plan.changes:
                change.path.parent.mkdir(parents=True, exist_ok=True)
                existing_mode = (
                    stat.S_IMODE(change.path.stat().st_mode)
                    if change.before is not None
                    else None
                )
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{change.path.name}.",
                    suffix=".skillcord.tmp",
                    dir=change.path.parent,
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(change.after)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if existing_mode is not None:
                        os.chmod(temporary, existing_mode)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
                staged.append((temporary, change.path))

            self._preflight(plan)
            for (temporary, target), change in zip(staged, plan.changes, strict=True):
                self._preflight_change(plan.project_root, change)
                os.replace(temporary, target)
            return ApplyResult(
                applied=True,
                changed_files=tuple(change.path for change in plan.changes),
            )
        finally:
            for temporary, _target in staged:
                temporary.unlink(missing_ok=True)

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
