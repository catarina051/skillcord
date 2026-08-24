"""Tests for persisted-decision reference validation."""

from skillcord.decisions.validation import validate_override_references
from skillcord.models.config import CapabilityOverride, OverrideConfig
from skillcord.models.status import CheckStatus


def test_missing_override_reference_is_failure() -> None:
    """A renamed preferred skill must fail closed instead of changing ownership."""

    checks = validate_override_references(
        overrides=OverrideConfig(
            schema_version=1,
            overrides={
                "test-driven-development": CapabilityOverride(prefer="ecc.old-tdd"),
            },
        ),
        discovered_ids={"superpowers.test-driven-development", "ecc.tdd"},
    )

    assert checks[0].id == "override.missing_reference"
    assert checks[0].status is CheckStatus.FAIL
    assert checks[0].details == {
        "capability_id": "test-driven-development",
        "field": "prefer",
        "skill_id": "ecc.old-tdd",
    }


def test_each_missing_preferred_or_suppressed_reference_has_a_stable_failure() -> None:
    """All stale decisions remain visible for a user to repair."""

    checks = validate_override_references(
        overrides=OverrideConfig(
            schema_version=1,
            overrides={
                "zebra": CapabilityOverride(
                    prefer="provider.missing-prefer",
                    suppress=["provider.missing-z", "provider.missing-a", "provider.present"],
                ),
                "alpha": CapabilityOverride(suppress=["provider.missing-b"]),
            },
        ),
        discovered_ids={"provider.present"},
    )

    assert [check.details for check in checks] == [
        {
            "capability_id": "alpha",
            "field": "suppress",
            "skill_id": "provider.missing-b",
        },
        {
            "capability_id": "zebra",
            "field": "prefer",
            "skill_id": "provider.missing-prefer",
        },
        {
            "capability_id": "zebra",
            "field": "suppress",
            "skill_id": "provider.missing-a",
        },
        {
            "capability_id": "zebra",
            "field": "suppress",
            "skill_id": "provider.missing-z",
        },
    ]
