"""Machine-readable runtime-status schemas."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class CheckStatus(StrEnum):
    """The possible outcomes for a status check."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    PARTIAL = "partial"


class CheckResult(BaseModel):
    """A stable status check outcome with structured details."""

    id: str
    status: CheckStatus
    details: dict[str, object] = Field(default_factory=dict)


class StatusReport(BaseModel):
    """A status report whose readiness is derived from its checks."""

    schema_version: Literal[1] = 1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Return true when no check has failed."""

        return all(check.status is not CheckStatus.FAIL for check in self.checks)

    @classmethod
    def from_checks(cls, checks: list[CheckResult]) -> "StatusReport":
        """Build a report from the supplied check outcomes."""

        return cls(checks=checks)
