"""Read-only planning for exact Skillcord-managed file changes."""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from skillcord.adapters.base import (
    AdapterContext,
    ArtifactOwnership,
    HarnessAdapter,
    compose_artifacts,
)
from skillcord.decisions.validation import validate_override_references
from skillcord.managed_blocks.writer import render_managed_update
from skillcord.models.capability import CapabilityGroup
from skillcord.models.status import CheckStatus


class SyncPlanError(ValueError):
    """Raised when a safe, deterministic sync plan cannot be produced."""


class UnsafeTargetPathError(SyncPlanError):
    """Raised when a generated artifact could escape the project root."""


class OwnedFileConflictError(SyncPlanError):
    """Raised when a dedicated file contains content Skillcord does not own."""


class UnresolvedConflictError(SyncPlanError):
    """Raised when project decisions are incomplete or stale."""


@dataclass(frozen=True)
class SyncContext:
    """Already-resolved state consumed by the read-only sync planner."""

    project_root: Path
    adapter_context: AdapterContext
    adapters: Sequence[HarnessAdapter]
    discovered_skill_ids: frozenset[str] | None = None
    conflict_groups: Sequence[CapabilityGroup] = ()
    non_interactive: bool = False
    owned_file_baselines: Mapping[Path, bytes] = field(default_factory=dict)

    def for_root(self, project_root: Path) -> SyncContext:
        """Return the same resolved state pointed at another fixture/project root."""

        return replace(self, project_root=project_root)


@dataclass(frozen=True)
class PlannedFileChange:
    """The exact bytes observed and proposed for one project file."""

    path: Path
    relative_path: Path
    ownership: ArtifactOwnership
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class SyncPlan:
    """An immutable preview that can be applied without provider re-resolution."""

    project_root: Path
    changes: tuple[PlannedFileChange, ...]

    def diff_text(self) -> str:
        """Return deterministic unified diffs for every planned byte change."""

        rendered: list[bytes] = []
        for change in self.changes:
            relative = change.relative_path.as_posix().encode("utf-8")
            before = change.before or b""
            diff_lines = difflib.diff_bytes(
                difflib.unified_diff,
                before.splitlines(keepends=True),
                change.after.splitlines(keepends=True),
                fromfile=b"/dev/null" if change.before is None else b"a/" + relative,
                tofile=b"b/" + relative,
            )
            for line in diff_lines:
                rendered.append(line)
                if not line.endswith((b"\r", b"\n")):
                    rendered.extend((b"\n", b"\\ No newline at end of file\n"))

        diff = b"".join(rendered)
        try:
            return diff.decode("utf-8")
        except UnicodeDecodeError:
            notice = (
                "# Non-UTF-8 diff bytes use unambiguous \\xNN escapes; "
                "literal backslashes are doubled.\n"
            )
            return notice + _escape_diff_bytes(diff)


def _escape_diff_bytes(diff: bytes) -> str:
    """Render arbitrary diff bytes reversibly without surrogate characters."""

    escaped: list[str] = []
    for value in diff:
        if value == 0x0A:
            escaped.append("\n")
        elif 0x20 <= value <= 0x7E and value != 0x5C:
            escaped.append(chr(value))
        elif value == 0x5C:
            escaped.append("\\\\")
        else:
            escaped.append(f"\\x{value:02x}")
    return "".join(escaped)


def resolve_target_within_root(project_root: Path, artifact_path: Path) -> tuple[Path, Path]:
    """Resolve a target and reject absolute, traversal, and symlink escapes."""

    root = project_root.resolve(strict=True)
    candidate = artifact_path if artifact_path.is_absolute() else root / artifact_path
    target = candidate.resolve(strict=False)
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise UnsafeTargetPathError(
            f"artifact target {artifact_path!s} escapes project root {root!s}"
        ) from error
    if relative == Path("."):
        raise UnsafeTargetPathError("artifact target must be a file below the project root")
    return target, relative


class SyncPlanner:
    """Compute exact adapter changes without mutating project or provider files."""

    def plan(self, context: SyncContext) -> SyncPlan:
        """Validate resolved state and return its deterministic file-change preview."""

        root = context.project_root.resolve(strict=True)
        self._validate_override_references(context)
        self._validate_noninteractive_conflicts(context)
        artifacts = compose_artifacts(context.adapters, context.adapter_context)

        changes: list[PlannedFileChange] = []
        targets_seen: set[Path] = set()
        for artifact in artifacts:
            target, relative = resolve_target_within_root(root, artifact.path)
            if target in targets_seen:
                raise SyncPlanError(f"multiple artifact paths resolve to {relative.as_posix()!r}")
            targets_seen.add(target)

            if target.exists() and not target.is_file():
                raise SyncPlanError(f"artifact target is not a regular file: {relative.as_posix()}")
            before = target.read_bytes() if target.exists() else None
            if artifact.ownership == "managed_block":
                after = render_managed_update(before or b"", artifact.content)
            else:
                after = self._plan_owned_file(context, artifact.path, target, before, artifact.content)
            if before == after:
                continue
            changes.append(
                PlannedFileChange(
                    path=target,
                    relative_path=relative,
                    ownership=artifact.ownership,
                    before=before,
                    after=after,
                )
            )

        return SyncPlan(project_root=root, changes=tuple(changes))

    @staticmethod
    def _validate_override_references(context: SyncContext) -> None:
        discovered = context.discovered_skill_ids
        if discovered is None:
            discovered = frozenset(
                {
                    *(skill.normalized_id for skill in context.adapter_context.active_skills),
                    *(
                        skill.normalized_id
                        for group in context.conflict_groups
                        for skill in group.candidates
                    ),
                }
            )
        failures = [
            check
            for check in validate_override_references(context.adapter_context.decisions, discovered)
            if check.status is CheckStatus.FAIL
        ]
        if failures:
            references = ", ".join(
                str(check.details["skill_id"])
                for check in failures
                if "skill_id" in check.details
            )
            raise UnresolvedConflictError(f"override references missing skill IDs: {references}")

    @staticmethod
    def _validate_noninteractive_conflicts(context: SyncContext) -> None:
        if not context.non_interactive:
            return
        overrides = context.adapter_context.decisions.overrides
        active_ids = {skill.normalized_id for skill in context.adapter_context.active_skills}
        for group in sorted(context.conflict_groups, key=lambda candidate: candidate.capability_id):
            override = overrides.get(group.capability_id)
            candidate_ids = {candidate.normalized_id for candidate in group.candidates}
            if override is None:
                if group.single_owner_required or group.action != "keep-all":
                    raise UnresolvedConflictError(
                        f"capability {group.capability_id!r} lacks a persisted decision"
                    )
                expected_active = candidate_ids
            else:
                references = (
                    {override.prefer} if override.prefer is not None else set()
                ) | set(override.suppress)
                invalid_references = sorted(references - candidate_ids)
                if invalid_references:
                    raise UnresolvedConflictError(
                        f"capability {group.capability_id!r} decision references non-candidates: "
                        f"{', '.join(invalid_references)}"
                    )
                if group.single_owner_required and override.prefer is None:
                    raise UnresolvedConflictError(
                        f"single-owner capability {group.capability_id!r} is unresolved"
                    )
                expected_active = (
                    {override.prefer}
                    if override.prefer is not None
                    else candidate_ids - set(override.suppress)
                )

            actual_active = candidate_ids & active_ids
            if actual_active != expected_active:
                raise UnresolvedConflictError(
                    f"capability {group.capability_id!r} active skills do not match its "
                    "persisted decision"
                )

    @staticmethod
    def _plan_owned_file(
        context: SyncContext,
        artifact_path: Path,
        target: Path,
        before: bytes | None,
        generated: bytes,
    ) -> bytes:
        if before is None or before == generated:
            return generated

        baseline = context.owned_file_baselines.get(artifact_path)
        if baseline is None:
            baseline = context.owned_file_baselines.get(target)
        if baseline is None or before != baseline:
            raise OwnedFileConflictError(
                f"refusing to overwrite unknown content in owned file {artifact_path.as_posix()!r}"
            )
        return generated
