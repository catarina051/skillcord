"""Deterministic planning for grouped conflict prompts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from skillcord.models.capability import CapabilityGroup


@dataclass(frozen=True)
class ConflictResolution:
    """The bounded prompt plan and keep-all defaults for one resolution session."""

    groups: list[CapabilityGroup]
    prompted_groups: list[CapabilityGroup]
    deferred_groups: list[CapabilityGroup]

    @property
    def prompted_count(self) -> int:
        """Return how many unresolved groups are eligible for prompting."""

        return len(self.prompted_groups)

    @property
    def deferred_count(self) -> int:
        """Return how many unresolved groups were retained without a prompt."""

        return len(self.deferred_groups)


class ConflictResolver:
    """Apply known actions and cap unresolved interactive prompts deterministically."""

    def __init__(self, question_budget: int = 10) -> None:
        if question_budget < 0:
            raise ValueError("question_budget must not be negative")
        self.question_budget = question_budget

    def resolve(
        self,
        groups: Sequence[CapabilityGroup],
        decisions: Mapping[str, str],
    ) -> ConflictResolution:
        """Return a stable prompt plan; every unanswered group stays keep-all."""

        resolved_groups: list[CapabilityGroup] = []
        prompted_groups: list[CapabilityGroup] = []
        deferred_groups: list[CapabilityGroup] = []

        for group in sorted(groups, key=lambda candidate: candidate.capability_id):
            decision = decisions.get(group.capability_id)
            action = decision if decision is not None else group.default_action
            resolved_group = group.model_copy(update={"action": action})
            resolved_groups.append(resolved_group)

            if decision is not None:
                continue
            if len(prompted_groups) < self.question_budget:
                prompted_groups.append(resolved_group)
            else:
                deferred_groups.append(resolved_group)

        return ConflictResolution(
            groups=resolved_groups,
            prompted_groups=prompted_groups,
            deferred_groups=deferred_groups,
        )
