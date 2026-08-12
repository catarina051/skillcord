"""Tests for deterministic doctor aggregation."""

from datetime import UTC, datetime
from pathlib import Path

from skillcord.adapters.base import GeneratedArtifact
from skillcord.models.capability import CapabilityGroup
from skillcord.models.config import (
    AIConfig,
    CapabilityOverride,
    OverrideConfig,
    ProjectConfig,
    ProjectInfo,
)
from skillcord.models.lock import LockFile
from skillcord.models.provider import ProviderSnapshot, SkillRecord, UnsupportedAsset
from skillcord.models.status import CheckStatus
from skillcord.status.doctor import Doctor, DoctorContext

NOW = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)


def _project(*desired_capabilities: str) -> ProjectConfig:
    return ProjectConfig(
        schema_version=1,
        project=ProjectInfo(intent="test"),
        ai=AIConfig(harnesses=["codex"], desired_capabilities=list(desired_capabilities)),
    )


def _empty_lock() -> LockFile:
    return LockFile(schema_version=1, skillcord_schema_version=1)


def _ecc_snapshot(tmp_path: Path) -> ProviderSnapshot:
    hook = tmp_path / "hooks" / "hooks.json"
    hook.parent.mkdir()
    hook.write_text("{}", encoding="utf-8")
    return ProviderSnapshot(
        provider_id="ecc",
        root_path=tmp_path,
        skills=[
            SkillRecord(
                provider_id="ecc",
                skill_id="security-review",
                name="Security review",
                source_path=tmp_path / "skills" / "security-review" / "SKILL.md",
                content_hash="b" * 64,
                capabilities={"security-review"},
            )
        ],
        partial_support=True,
        unsupported_assets=[
            UnsupportedAsset(
                kind="executable-hook",
                source_path=hook,
                reason="V1 does not execute provider hooks",
            )
        ],
    )


def test_ecc_hooks_are_partial_when_requested_capabilities_are_declarative(
    tmp_path: Path,
) -> None:
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project("security-review"),
            providers=[_ecc_snapshot(tmp_path)],
            lock=_empty_lock(),
        )
    )

    check = next(check for check in report.checks if check.id == "provider.partial_support")
    assert check.status is CheckStatus.PARTIAL
    assert report.ready is True


def test_requested_executable_hook_capability_fails(tmp_path: Path) -> None:
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project("executable-hook"),
            providers=[_ecc_snapshot(tmp_path)],
            lock=_empty_lock(),
        )
    )

    check = next(check for check in report.checks if check.id == "provider.partial_support")
    assert check.status is CheckStatus.FAIL
    assert check.details["unsatisfied_capabilities"] == ["executable-hook"]
    assert report.ready is False


def test_doctor_emits_every_stable_check_id_in_sorted_order(tmp_path: Path) -> None:
    humanizer_skill = SkillRecord(
        provider_id="humanizer",
        skill_id="humanizer",
        name="Humanizer",
        source_path=tmp_path / "SKILL.md",
        content_hash="a" * 64,
        optional=True,
        execution_mode="harness_only",
    )
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            providers=[
                ProviderSnapshot(
                    provider_id="humanizer",
                    root_path=tmp_path,
                    skills=[humanizer_skill],
                )
            ],
            lock=_empty_lock(),
        )
    )

    expected = {
        "schema.project",
        "provider.presence",
        "lock.integrity",
        "override.missing_reference",
        "adapter.drift",
        "conflicts.unresolved",
        "provider.partial_support",
        "humanizer.policy",
    }
    assert expected <= {check.id for check in report.checks}
    assert [check.id for check in report.checks] == sorted(check.id for check in report.checks)
    assert report.timestamp == NOW


def test_warnings_and_partial_checks_do_not_make_report_not_ready(tmp_path: Path) -> None:
    group = CapabilityGroup(capability_id="tdd", action="keep-all")
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            providers=[_ecc_snapshot(tmp_path)],
            lock=_empty_lock(),
            conflict_groups=[group],
        )
    )

    assert report.ready is True
    assert {check.status for check in report.checks} >= {
        CheckStatus.WARNING,
        CheckStatus.PARTIAL,
    }


def test_unresolved_single_owner_conflict_fails() -> None:
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            lock=_empty_lock(),
            conflict_groups=[
                CapabilityGroup(
                    capability_id="exclusive",
                    action="keep-all",
                    single_owner_required=True,
                )
            ],
        )
    )

    assert next(
        check.status for check in report.checks if check.id == "conflicts.unresolved"
    ) is CheckStatus.FAIL
    assert report.ready is False


def test_single_owner_override_without_preferred_owner_still_fails() -> None:
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            lock=_empty_lock(),
            overrides=OverrideConfig(
                schema_version=1,
                overrides={"exclusive": CapabilityOverride()},
            ),
            conflict_groups=[
                CapabilityGroup(
                    capability_id="exclusive",
                    action="keep-all",
                    single_owner_required=True,
                )
            ],
        )
    )

    check = next(check for check in report.checks if check.id == "conflicts.unresolved")
    assert check.status is CheckStatus.FAIL
    assert check.details["blocking"] == ["exclusive"]


def test_adapter_drift_is_detected_without_modifying_file(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"unexpected\n")
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            lock=_empty_lock(),
            project_root=tmp_path,
            adapter_artifacts=[
                GeneratedArtifact(
                    path=Path("AGENTS.md"),
                    ownership="owned_file",
                    content=b"expected\n",
                    source_capability_ids=(),
                )
            ],
        )
    )

    assert agents.read_bytes() == b"unexpected\n"
    check = next(check for check in report.checks if check.id == "adapter.drift")
    assert check.status is CheckStatus.FAIL
    assert check.details["paths"] == ["AGENTS.md"]


def test_invalid_humanizer_execution_policy_fails(tmp_path: Path) -> None:
    unsafe = SkillRecord(
        provider_id="humanizer",
        skill_id="humanizer",
        name="Humanizer",
        source_path=tmp_path / "SKILL.md",
        content_hash="a" * 64,
        optional=False,
        execution_mode="provider",
    )
    report = Doctor(clock=lambda: NOW).run(
        DoctorContext(
            project=_project(),
            providers=[ProviderSnapshot(provider_id="humanizer", root_path=tmp_path, skills=[unsafe])],
            lock=_empty_lock(),
        )
    )

    check = next(check for check in report.checks if check.id == "humanizer.policy")
    assert check.status is CheckStatus.FAIL
    assert report.ready is False
