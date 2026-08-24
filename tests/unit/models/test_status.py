from skillcord.models.status import CheckResult, CheckStatus, StatusReport


def test_ready_is_derived_from_checks() -> None:
    report = StatusReport.from_checks(
        [
            CheckResult(id="lock.integrity", status=CheckStatus.PASS, details={}),
            CheckResult(id="override.missing_reference", status=CheckStatus.FAIL, details={}),
        ]
    )
    assert report.ready is False
