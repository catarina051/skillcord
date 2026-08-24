"""Integration checks for provider-update drift and stale decisions."""

from datetime import UTC, datetime
from hashlib import sha256

from skillcord.decisions.validation import validate_override_references
from skillcord.locking.service import LockService
from skillcord.models.config import (
    AIConfig,
    CapabilityOverride,
    OverrideConfig,
    ProjectConfig,
    ProjectInfo,
)
from skillcord.models.provider import ProviderComponent, ProviderSnapshot, SkillRecord
from skillcord.models.status import CheckStatus
from skillcord.status.doctor import Doctor, DoctorContext


def test_doctor_reports_provider_hash_drift_and_missing_override_reference(tmp_path) -> None:
    provider_root = tmp_path / "ecc"
    skill_path = provider_root / "skills" / "tdd" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = b"---\nname: TDD\n---\n"
    skill_path.write_bytes(original)
    skill = SkillRecord(
        provider_id="ecc",
        skill_id="tdd",
        name="TDD",
        source_path=skill_path,
        content_hash=sha256(original).hexdigest(),
        capabilities={"tdd"},
    )
    snapshot = ProviderSnapshot(
        provider_id="ecc",
        root_path=provider_root,
        skills=[skill],
        components=[
            ProviderComponent(
                component_id="ecc.skills",
                source_path=provider_root,
                artifact_hashes={skill_path: sha256(original).hexdigest()},
            )
        ],
    )
    lock = LockService().build([snapshot], resolved_override_ids={"ecc.old-tdd"})
    overrides = OverrideConfig(
        schema_version=1,
        overrides={"tdd": CapabilityOverride(prefer="ecc.old-tdd")},
    )
    assert validate_override_references(overrides, {skill.normalized_id})[0].status is CheckStatus.FAIL
    skill_path.write_bytes(b"changed by provider updater\n")

    report = Doctor(clock=lambda: datetime(2026, 8, 11, 17, 0, tzinfo=UTC)).run(
        DoctorContext(
            project=ProjectConfig(
                schema_version=1,
                project=ProjectInfo(intent="test"),
                ai=AIConfig(harnesses=[], desired_capabilities=["tdd"]),
            ),
            providers=[snapshot],
            lock=lock,
            overrides=overrides,
        )
    )

    by_id = {check.id: check for check in report.checks}
    assert by_id["lock.integrity"].status is CheckStatus.FAIL
    assert by_id["override.missing_reference"].status is CheckStatus.FAIL
    assert by_id["override.missing_reference"].details["skill_ids"] == ["ecc.old-tdd"]
    assert report.ready is False
