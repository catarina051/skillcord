"""Stable human-readable renderers for the thin CLI layer."""

from __future__ import annotations

from collections.abc import Sequence

from skillcord.discovery.service import DiscoveryResult
from skillcord.history.store import HistoryEntry
from skillcord.models.capability import CapabilityGroup
from skillcord.models.status import StatusReport


def render_scan(result: DiscoveryResult) -> str:
    """Render discovered inventory without exposing filesystem-dependent ordering."""

    lines = ["Providers:"]
    lines.extend(f"- {provider.provider_id}" for provider in result.providers)
    lines.append("Harnesses:")
    lines.extend(f"- {harness.harness_id}" for harness in result.harnesses)
    lines.extend(f"Warning: {warning.message}" for warning in result.warnings)
    lines.extend(
        f"Unsupported {item.provider_id}: {item.reason}" for item in result.unsupported_layouts
    )
    return "\n".join(lines) + "\n"


def render_conflicts(groups: Sequence[CapabilityGroup], question_budget: int) -> str:
    """Render capability groups once, with bounded interactive prompt information."""

    ordered = sorted(groups, key=lambda group: group.capability_id)
    lines = [f"Question budget: {question_budget}"]
    for group in ordered:
        candidates = ", ".join(candidate.normalized_id for candidate in group.candidates)
        lines.append(f"{group.capability_id}: {candidates}")
    lines.append(f"Keep-all beyond budget: {max(0, len(ordered) - question_budget)}")
    return "\n".join(lines) + "\n"


def render_status(report: StatusReport) -> str:
    """Render a concise, deterministic readiness report."""

    lines = [f"Ready: {'yes' if report.ready else 'no'}"]
    lines.extend(f"{check.status.value}: {check.id}" for check in report.checks)
    return "\n".join(lines) + "\n"


def render_history(entries: Sequence[HistoryEntry]) -> str:
    """Render history entries using their validated public fields."""

    if not entries:
        return "No history entries.\n"
    return "".join(
        f"{entry.timestamp} {entry.action}: {', '.join(entry.files_changed)}\n" for entry in entries
    )
