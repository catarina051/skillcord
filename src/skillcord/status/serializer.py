"""Deterministic JSON serialization for machine-readable runtime status."""

import json
from datetime import UTC
from typing import Any

from skillcord.models.status import StatusReport


def serialize_status(report: StatusReport) -> str:
    """Serialize a status report with stable fields, ordering, and UTC timestamp."""

    timestamp = report.timestamp
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("status timestamp must be timezone-aware")
    timestamp_text = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    checks = sorted(
        (
            {
                "id": check.id,
                "status": check.status.value,
                "details": check.model_dump(mode="json")["details"],
            }
            for check in report.checks
        ),
        key=lambda check: (
            str(check["id"]),
            json.dumps(check["details"], sort_keys=True, separators=(",", ":")),
        ),
    )
    payload: dict[str, Any] = {
        "schema_version": report.schema_version,
        "timestamp": timestamp_text,
        "ready": report.ready,
        "checks": checks,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
