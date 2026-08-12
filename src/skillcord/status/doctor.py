"""AI-runtime readiness aggregation with injected local evidence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from skillcord.adapters.base import GeneratedArtifact
from skillcord.locking.service import LockService
from skillcord.models.capability import CapabilityGroup
from skillcord.models.config import OverrideConfig, ProjectConfig
from skillcord.models.lock import LockFile
from skillcord.models.provider import ProviderSnapshot
from skillcord.models.status import CheckResult, StatusReport
from skillcord.status.checks import (
    check_adapter_drift,
    check_humanizer_policy,
    check_lock_integrity,
    check_override_references,
    check_project_schema,
    check_provider_partial_support,
    check_provider_presence,
    check_unresolved_conflicts,
    discovered_skill_ids,
    ensure_unique_check_ids,
)


@dataclass(frozen=True)
class DoctorContext:
    """Already-discovered V1 state consumed by the read-only doctor."""

    project: ProjectConfig | None = None
    project_error: str | None = None
    providers: Sequence[ProviderSnapshot] = ()
    required_provider_ids: frozenset[str] = frozenset()
    lock: LockFile | None = None
    overrides: OverrideConfig = field(
        default_factory=lambda: OverrideConfig(schema_version=1)
    )
    conflict_groups: Sequence[CapabilityGroup] = ()
    project_root: Path | None = None
    adapter_artifacts: Sequence[GeneratedArtifact] = ()


class Doctor:
    """Aggregate stable status checks without discovery, execution, or mutation."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        lock_service: LockService | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_service = lock_service or LockService()

    def run(self, context: DoctorContext) -> StatusReport:
        """Return a timestamped, deterministically ordered readiness report."""

        skill_ids = discovered_skill_ids(context.providers)
        desired_capabilities = (
            context.project.ai.desired_capabilities if context.project is not None else ()
        )
        checks: list[CheckResult] = [
            check_project_schema(context.project, context.project_error),
            check_provider_presence(context.providers, context.required_provider_ids),
            check_lock_integrity(context.lock, self._lock_service),
            check_override_references(context.overrides, skill_ids),
            check_adapter_drift(context.project_root, context.adapter_artifacts),
            check_unresolved_conflicts(context.conflict_groups, context.overrides),
            check_provider_partial_support(context.providers, desired_capabilities),
            check_humanizer_policy(context.providers),
        ]
        ensure_unique_check_ids(checks)
        checks.sort(key=lambda check: check.id)
        return StatusReport(timestamp=self._clock(), checks=checks)
