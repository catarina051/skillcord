"""Validation for persisted override references after provider discovery."""

from collections.abc import Collection

from skillcord.models.config import OverrideConfig
from skillcord.models.status import CheckResult, CheckStatus


def validate_override_references(
    overrides: OverrideConfig,
    discovered_ids: Collection[str],
) -> list[CheckResult]:
    """Report every stale preferred or suppressed normalized skill ID.

    A missing reference can indicate provider removal or a renamed skill.  It is
    intentionally a failure: retaining the stale override makes the required
    user repair explicit instead of silently altering conflict ownership.
    """

    discovered = set(discovered_ids)
    checks: list[CheckResult] = []

    for capability_id, override in sorted(overrides.overrides.items()):
        references = _references_for(override.prefer, override.suppress)
        for field, skill_id in references:
            if skill_id not in discovered:
                checks.append(
                    CheckResult(
                        id="override.missing_reference",
                        status=CheckStatus.FAIL,
                        details={
                            "capability_id": capability_id,
                            "field": field,
                            "skill_id": skill_id,
                        },
                    )
                )

    return checks


def _references_for(prefer: str | None, suppress: list[str]) -> list[tuple[str, str]]:
    """Return a capability's references in a deterministic validation order."""

    references: list[tuple[str, str]] = []
    if prefer is not None:
        references.append(("prefer", prefer))
    references.extend(("suppress", skill_id) for skill_id in sorted(suppress))
    return references
