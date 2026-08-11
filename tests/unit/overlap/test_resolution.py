"""Tests for deterministic grouped conflict prompt budgeting."""

from pathlib import Path

from skillcord.models.capability import CapabilityGroup
from skillcord.models.provider import SkillRecord
from skillcord.overlap.resolution import ConflictResolver


def _groups(count: int) -> list[CapabilityGroup]:
    return [
        CapabilityGroup(
            capability_id=f"capability-{index:02d}",
            candidates=[
                SkillRecord(
                    provider_id="provider",
                    skill_id=f"skill-{index:02d}",
                    name=f"skill-{index:02d}",
                    source_path=Path(f"/provider/skill-{index:02d}/SKILL.md"),
                    content_hash="a" * 64,
                )
            ],
        )
        for index in range(count)
    ]


def test_resolver_prompts_at_most_ten_groups() -> None:
    """A resolver without a budget would overwhelm an interactive session."""

    result = ConflictResolver(question_budget=10).resolve(groups=_groups(12), decisions={})

    assert result.prompted_count == 10
    assert result.deferred_count == 2
    assert [group.capability_id for group in result.deferred_groups] == [
        "capability-10",
        "capability-11",
    ]
    assert all(group.action == "keep-all" for group in result.deferred_groups)


def test_resolver_applies_known_decisions_without_consuming_prompt_budget() -> None:
    """A persisted decision must be reused instead of being prompted again."""

    result = ConflictResolver(question_budget=1).resolve(
        groups=_groups(2),
        decisions={"capability-00": "prefer:provider.skill-00"},
    )

    assert result.prompted_count == 1
    assert result.deferred_count == 0
    assert result.groups[0].action == "prefer:provider.skill-00"
    assert [group.capability_id for group in result.prompted_groups] == ["capability-01"]
