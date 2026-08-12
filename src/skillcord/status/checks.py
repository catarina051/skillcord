"""Pure, deterministic checks used by the AI-runtime doctor."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from skillcord.adapters.base import GeneratedArtifact
from skillcord.decisions.validation import validate_override_references
from skillcord.discovery.harnesses import HarnessDetection
from skillcord.locking.service import LockService
from skillcord.managed_blocks.writer import render_managed_update
from skillcord.models.capability import CapabilityGroup
from skillcord.models.config import OverrideConfig, ProjectConfig
from skillcord.models.lock import LockFile
from skillcord.models.provider import ProviderSnapshot
from skillcord.models.status import CheckResult, CheckStatus

STABLE_CHECK_IDS = frozenset(
    {
        "schema.project",
        "harness.availability",
        "capability.satisfaction",
        "provider.presence",
        "lock.integrity",
        "override.missing_reference",
        "adapter.drift",
        "conflicts.unresolved",
        "provider.partial_support",
        "humanizer.policy",
    }
)


def check_project_schema(
    project: ProjectConfig | None,
    error: str | None,
) -> CheckResult:
    """Report whether project configuration was parsed as the V1 schema."""

    if project is None:
        return CheckResult(
            id="schema.project",
            status=CheckStatus.FAIL,
            details={"error": error or "project configuration is unavailable"},
        )
    return CheckResult(
        id="schema.project",
        status=CheckStatus.PASS,
        details={"schema_version": project.schema_version},
    )


def check_provider_presence(
    providers: Sequence[ProviderSnapshot],
    required_provider_ids: Collection[str],
    lock: LockFile | None,
) -> CheckResult:
    """Report required provider roots that were not discovered."""

    present = {provider.provider_id for provider in providers}
    required = set(required_provider_ids)
    if lock is not None:
        required.update(lock.providers)
    missing = sorted(required - present)
    return CheckResult(
        id="provider.presence",
        status=CheckStatus.FAIL if missing else CheckStatus.PASS,
        details={
            "present": sorted(present),
            "required": sorted(required),
            "missing": missing,
        },
    )


def check_harness_availability(
    project: ProjectConfig | None,
    harnesses: Sequence[HarnessDetection],
) -> CheckResult:
    """Require local discovery evidence for every requested harness."""

    requested = set(project.ai.harnesses) if project is not None else set()
    available = {harness.harness_id for harness in harnesses}
    missing = sorted(requested - available)
    return CheckResult(
        id="harness.availability",
        status=CheckStatus.FAIL if missing else CheckStatus.PASS,
        details={
            "requested": sorted(requested),
            "available": sorted(available),
            "missing": missing,
        },
    )


def check_capability_satisfaction(
    project: ProjectConfig | None,
    providers: Sequence[ProviderSnapshot],
) -> CheckResult:
    """Match requested capabilities only against explicit provider skill evidence."""

    requested = set(project.ai.desired_capabilities) if project is not None else set()
    available = {
        evidence
        for provider in providers
        for skill in provider.skills
        for evidence in {skill.normalized_id, skill.skill_id, *skill.capabilities}
    }
    missing = sorted(requested - available)
    return CheckResult(
        id="capability.satisfaction",
        status=CheckStatus.FAIL if missing else CheckStatus.PASS,
        details={
            "requested": sorted(requested),
            "available": sorted(available),
            "missing": missing,
        },
    )


def check_lock_integrity(
    lock: LockFile | None,
    lock_service: LockService,
    providers: Sequence[ProviderSnapshot],
    override_reference_ids: Collection[str],
) -> CheckResult:
    """Check lock drift plus exact current provider and decision coverage."""

    if lock is None:
        return CheckResult(
            id="lock.integrity",
            status=CheckStatus.FAIL,
            details={
                "reason": "lock_missing",
                "failures": [],
                "missing_providers": sorted(provider.provider_id for provider in providers),
                "extra_providers": [],
                "missing_resolved_ids": sorted(override_reference_ids),
                "extra_resolved_ids": [],
            },
        )

    evidence = lock_service.check_drift(lock)
    failures = [check for check in evidence if check.status is CheckStatus.FAIL]
    discovered_provider_ids = {provider.provider_id for provider in providers}
    locked_provider_ids = set(lock.providers)
    current_resolved_ids = set(override_reference_ids)
    missing_providers = sorted(discovered_provider_ids - locked_provider_ids)
    extra_providers = sorted(locked_provider_ids - discovered_provider_ids)
    missing_resolved_ids = sorted(current_resolved_ids - lock.resolved_ids)
    extra_resolved_ids = sorted(lock.resolved_ids - current_resolved_ids)
    coverage_failed = any(
        (missing_providers, extra_providers, missing_resolved_ids, extra_resolved_ids)
    )
    return CheckResult(
        id="lock.integrity",
        status=CheckStatus.FAIL if failures or coverage_failed else CheckStatus.PASS,
        details={
            "providers_checked": len(lock.providers),
            "artifacts_checked": sum(
                1
                for provider in lock.providers.values()
                for component in provider.components
                for _ in component.artifacts.values()
            ),
            "failures": [
                {"id": failure.id, "details": failure.details} for failure in failures
            ],
            "missing_providers": missing_providers,
            "extra_providers": extra_providers,
            "missing_resolved_ids": missing_resolved_ids,
            "extra_resolved_ids": extra_resolved_ids,
        },
    )


def check_override_references(
    overrides: OverrideConfig,
    discovered_ids: Collection[str],
) -> CheckResult:
    """Collapse stale override references into one stable failure."""

    failures = validate_override_references(overrides, discovered_ids)
    return CheckResult(
        id="override.missing_reference",
        status=CheckStatus.FAIL if failures else CheckStatus.PASS,
        details={
            "count": len(failures),
            "skill_ids": sorted(
                {
                    str(check.details["skill_id"])
                    for check in failures
                    if "skill_id" in check.details
                }
            ),
            "references": [check.details for check in failures],
        },
    )


def check_adapter_drift(
    project_root: Path | None,
    artifacts: Sequence[GeneratedArtifact],
) -> CheckResult:
    """Compare planned adapter bytes with disk without modifying either."""

    if not artifacts:
        return CheckResult(
            id="adapter.drift",
            status=CheckStatus.PASS,
            details={"files_checked": 0, "paths": []},
        )
    if project_root is None:
        return CheckResult(
            id="adapter.drift",
            status=CheckStatus.FAIL,
            details={"reason": "project_root_missing", "paths": []},
        )

    root = project_root.resolve()
    drifted: list[str] = []
    reasons: dict[str, str] = {}
    checked = 0
    for artifact in sorted(artifacts, key=lambda item: item.path.as_posix()):
        relative = artifact.path
        candidate = relative if relative.is_absolute() else root / relative
        target = candidate.resolve(strict=False)
        try:
            display_path = target.relative_to(root).as_posix()
        except ValueError:
            display_path = artifact.path.as_posix()
            drifted.append(display_path)
            reasons[display_path] = "path_outside_project"
            continue

        checked += 1
        if not target.is_file():
            drifted.append(display_path)
            reasons[display_path] = "file_missing"
            continue
        try:
            actual = target.read_bytes()
            expected = (
                render_managed_update(actual, artifact.content)
                if artifact.ownership == "managed_block"
                else artifact.content
            )
        except OSError as error:
            drifted.append(display_path)
            reasons[display_path] = f"file_unreadable:{type(error).__name__}"
        except ValueError as error:
            drifted.append(display_path)
            reasons[display_path] = f"managed_block_invalid:{type(error).__name__}"
        else:
            if actual != expected:
                drifted.append(display_path)
                reasons[display_path] = "content_mismatch"

    return CheckResult(
        id="adapter.drift",
        status=CheckStatus.FAIL if drifted else CheckStatus.PASS,
        details={
            "files_checked": checked,
            "paths": sorted(drifted),
            "reasons": {path: reasons[path] for path in sorted(reasons)},
        },
    )


def check_unresolved_conflicts(
    groups: Sequence[CapabilityGroup],
    overrides: OverrideConfig,
) -> CheckResult:
    """Keep ordinary unresolved groups visible and fail only single-owner policy."""

    unresolved: set[str] = set()
    blocking: set[str] = set()
    invalid_references: list[dict[str, object]] = []
    for group in sorted(groups, key=lambda item: item.capability_id):
        override = overrides.overrides.get(group.capability_id)
        if override is None:
            unresolved.add(group.capability_id)
            if group.single_owner_required:
                blocking.add(group.capability_id)
            continue

        candidate_ids = {candidate.normalized_id for candidate in group.candidates}
        references = (
            ({override.prefer} if override.prefer is not None else set())
            | set(override.suppress)
        )
        invalid = sorted(references - candidate_ids)
        if invalid:
            unresolved.add(group.capability_id)
            blocking.add(group.capability_id)
            invalid_references.append(
                {"capability_id": group.capability_id, "skill_ids": invalid}
            )
        if group.single_owner_required and override.prefer is None:
            unresolved.add(group.capability_id)
            blocking.add(group.capability_id)

    unresolved_list = sorted(unresolved)
    blocking_list = sorted(blocking)
    if blocking_list:
        status = CheckStatus.FAIL
    elif unresolved_list:
        status = CheckStatus.WARNING
    else:
        status = CheckStatus.PASS
    return CheckResult(
        id="conflicts.unresolved",
        status=status,
        details={
            "count": len(unresolved_list),
            "capabilities": unresolved_list,
            "blocking": blocking_list,
            "invalid_references": invalid_references,
        },
    )


def check_provider_partial_support(
    providers: Sequence[ProviderSnapshot],
    desired_capabilities: Collection[str],
) -> CheckResult:
    """Surface unsupported assets and fail when their capability is explicitly required."""

    partial = [provider for provider in providers if provider.partial_support]
    unsupported_kinds = {
        asset.kind for provider in partial for asset in provider.unsupported_assets
    }
    unsatisfied = sorted(set(desired_capabilities) & unsupported_kinds)
    if unsatisfied:
        status = CheckStatus.FAIL
    elif partial:
        status = CheckStatus.PARTIAL
    else:
        status = CheckStatus.PASS
    return CheckResult(
        id="provider.partial_support",
        status=status,
        details={
            "providers": sorted(provider.provider_id for provider in partial),
            "unsupported_asset_kinds": sorted(unsupported_kinds),
            "unsatisfied_capabilities": unsatisfied,
        },
    )


def check_humanizer_policy(providers: Sequence[ProviderSnapshot]) -> CheckResult:
    """Require the plan-governed optional, harness-only Humanizer boundary."""

    humanizer = [provider for provider in providers if provider.provider_id == "humanizer"]
    invalid = sorted(
        skill.normalized_id
        for provider in humanizer
        for skill in provider.skills
        if not skill.optional or skill.execution_mode != "harness_only"
    )
    return CheckResult(
        id="humanizer.policy",
        status=CheckStatus.FAIL if invalid else CheckStatus.PASS,
        details={"enabled": bool(humanizer), "invalid_skill_ids": invalid},
    )


def discovered_skill_ids(providers: Sequence[ProviderSnapshot]) -> frozenset[str]:
    """Return every normalized skill ID in deterministic provider snapshots."""

    return frozenset(skill.normalized_id for provider in providers for skill in provider.skills)


def override_reference_ids(overrides: OverrideConfig) -> frozenset[str]:
    """Return every normalized skill ID referenced by current decisions."""

    return frozenset(
        skill_id
        for override in overrides.overrides.values()
        for skill_id in (
            *([override.prefer] if override.prefer is not None else []),
            *override.suppress,
        )
    )


def ensure_unique_check_ids(checks: Sequence[CheckResult]) -> None:
    """Fail programming errors that would make status consumers ambiguous."""

    counts: dict[str, int] = {}
    for check in checks:
        counts[check.id] = counts.get(check.id, 0) + 1
    duplicates = sorted(check_id for check_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate doctor check IDs: {', '.join(duplicates)}")
