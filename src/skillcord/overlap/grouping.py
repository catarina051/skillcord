"""Deterministic capability overlap grouping without semantic inference."""

from collections.abc import ItemsView, Mapping, Sequence
from typing import Any

from skillcord.models.capability import CapabilityGroup
from skillcord.models.config import CapabilityOverride, OverrideConfig
from skillcord.models.provider import SkillRecord
from skillcord.overlap.aliases import AliasRegistry

OverrideMapping = Mapping[str, CapabilityOverride | Mapping[str, Any]]


def group_capabilities(
    skills: Sequence[SkillRecord],
    aliases: AliasRegistry,
    overrides: OverrideMapping | OverrideConfig,
) -> list[CapabilityGroup]:
    """Return sorted groups supported by explicit identifier, tag, alias, or override evidence."""

    skills_by_id = {skill.normalized_id: skill for skill in skills}
    grouped_candidates: dict[str, dict[str, SkillRecord]] = {}

    for skill in skills_by_id.values():
        for signal in _skill_signals(skill):
            capability_id = aliases.canonicalize(signal)
            if capability_id:
                grouped_candidates.setdefault(capability_id, {})[skill.normalized_id] = skill

    for capability_id, override in _override_items(overrides):
        linked_skills = _referenced_skills(override, skills_by_id)
        if linked_skills:
            bucket = grouped_candidates.setdefault(aliases.canonicalize(capability_id), {})
            bucket.update({skill.normalized_id: skill for skill in linked_skills})

    return [
        CapabilityGroup(
            capability_id=capability_id,
            candidates=sorted(candidates.values(), key=lambda skill: skill.normalized_id),
        )
        for capability_id, candidates in sorted(grouped_candidates.items())
        if len(candidates) > 1
    ]


def _skill_signals(skill: SkillRecord) -> set[str]:
    """Return the only signals V1 permits for deterministic overlap evidence."""

    return {skill.skill_id, skill.name, *skill.capabilities}


def _override_items(
    overrides: OverrideMapping | OverrideConfig,
) -> ItemsView[str, CapabilityOverride | Mapping[str, Any]]:
    """Expose override mappings while accepting the canonical configuration model."""

    override_mapping: OverrideMapping
    if isinstance(overrides, OverrideConfig):
        override_mapping = overrides.overrides
    else:
        override_mapping = overrides
    return override_mapping.items()


def _referenced_skills(
    override: CapabilityOverride | Mapping[str, Any],
    skills_by_id: Mapping[str, SkillRecord],
) -> list[SkillRecord]:
    """Find existing skill IDs directly linked by a persisted override."""

    if isinstance(override, CapabilityOverride):
        prefer = override.prefer
        suppress = override.suppress
    else:
        raw_prefer = override.get("prefer")
        raw_suppress = override.get("suppress", [])
        prefer = raw_prefer if isinstance(raw_prefer, str) else None
        suppress = raw_suppress if isinstance(raw_suppress, list) else []

    referenced_ids = {skill_id for skill_id in suppress if isinstance(skill_id, str)}
    if prefer is not None:
        referenced_ids.add(prefer)
    return [skills_by_id[skill_id] for skill_id in sorted(referenced_ids) if skill_id in skills_by_id]
