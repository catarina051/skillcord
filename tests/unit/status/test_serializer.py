"""Tests for deterministic machine-readable runtime status."""

import json
from datetime import UTC, datetime

import pytest

from skillcord.models.status import CheckResult, CheckStatus, StatusReport
from skillcord.status.serializer import serialize_status


@pytest.fixture
def status_report() -> StatusReport:
    return StatusReport(
        timestamp=datetime(2026, 8, 11, 17, 0, tzinfo=UTC),
        checks=[
            CheckResult(id="schema.project", status=CheckStatus.PASS),
            CheckResult(
                id="provider.partial_support",
                status=CheckStatus.PARTIAL,
                details={"providers": ["ecc"]},
            ),
        ],
    )


def test_status_json_has_stable_machine_fields(status_report: StatusReport) -> None:
    payload = json.loads(serialize_status(status_report))
    assert payload["schema_version"] == 1
    assert payload["timestamp"].endswith("Z")
    assert isinstance(payload["checks"], list)
    assert all("id" in check and "status" in check for check in payload["checks"])


def test_status_json_is_deterministic_and_sorts_checks() -> None:
    report = StatusReport(
        timestamp=datetime(2026, 8, 11, 17, 0, tzinfo=UTC),
        checks=[
            CheckResult(id="z.check", status=CheckStatus.WARNING, details={"z": 2, "a": 1}),
            CheckResult(id="a.check", status=CheckStatus.PASS),
        ],
    )

    first = serialize_status(report)
    second = serialize_status(report.model_copy(deep=True))

    assert first == second
    assert [check["id"] for check in json.loads(first)["checks"]] == ["a.check", "z.check"]
    assert json.loads(first)["ready"] is True


def test_status_json_rejects_naive_timestamp() -> None:
    report = StatusReport(timestamp=datetime(2026, 8, 11, 17, 0), checks=[])  # noqa: DTZ001

    with pytest.raises(ValueError, match="timezone-aware"):
        serialize_status(report)
