"""Tests for deterministic capability overlap grouping."""

from pathlib import Path

from skillcord.models.provider import SkillRecord
from skillcord.overlap.aliases import AliasRegistry
from skillcord.overlap.grouping import group_capabilities


def _skill(
    provider_id: str,
    skill_id: str,
    capabilities: set[str],
    *,
    description: str | None = None,
) -> SkillRecord:
    return SkillRecord(
        provider_id=provider_id,
        skill_id=skill_id,
        name=skill_id,
        description=description,
        source_path=Path(f"/{provider_id}/{skill_id}/SKILL.md"),
        content_hash="a" * 64,
        capabilities=capabilities,
    )


def _aliases() -> AliasRegistry:
    return AliasRegistry.from_path(Path("registry/aliases/capabilities.yaml"))


def test_tdd_aliases_group_across_providers() -> None:
    """Dropping alias canonicalization would split a known TDD overlap."""

    skills = [
        _skill("superpowers", "test-driven-development", {"test-driven-development"}),
        _skill("ecc", "tdd", {"tdd"}),
    ]

    groups = group_capabilities(skills, _aliases(), overrides={})

    assert len(groups) == 1
    assert groups[0].capability_id == "test-driven-development"
    assert groups[0].default_action == "keep-all"
    assert [candidate.normalized_id for candidate in groups[0].candidates] == [
        "ecc.tdd",
        "superpowers.test-driven-development",
    ]


def test_similar_descriptions_with_unrelated_names_and_tags_do_not_group() -> None:
    """Accidental description-similarity matching must not create a conflict."""

    skills = [
        _skill(
            "alpha",
            "repository-audit",
            {"repository-audit"},
            description="Review code carefully before changing it.",
        ),
        _skill(
            "beta",
            "implementation-checklist",
            {"implementation-checklist"},
            description="Review code carefully before changing it.",
        ),
    ]

    assert group_capabilities(skills, _aliases(), overrides={}) == []


def test_override_references_group_otherwise_unrelated_skills() -> None:
    """Removing override-reference evidence would hide a prior human-linked conflict."""

    skills = [
        _skill("beta", "second-workflow", {"second-workflow"}),
        _skill("alpha", "first-workflow", {"first-workflow"}),
    ]

    groups = group_capabilities(
        skills,
        _aliases(),
        overrides={
            "combined-workflow": {
                "prefer": "alpha.first-workflow",
                "suppress": ["beta.second-workflow"],
            }
        },
    )

    assert [group.capability_id for group in groups] == ["combined-workflow"]
    assert [candidate.normalized_id for candidate in groups[0].candidates] == [
        "alpha.first-workflow",
        "beta.second-workflow",
    ]


def test_groups_and_candidates_have_stable_identifier_ordering() -> None:
    """Input order must not make rendered conflict output flap."""

    skills = [
        _skill("superpowers", "test-driven-development", {"test-driven-development"}),
        _skill("ecc", "tdd", {"tdd"}),
        _skill("superpowers", "code-review", {"code-review"}),
        _skill("ecc", "review-code", {"review-code"}),
    ]

    groups = group_capabilities(list(reversed(skills)), _aliases(), overrides={})

    assert [group.capability_id for group in groups] == [
        "code-review",
        "test-driven-development",
    ]
    assert [candidate.normalized_id for candidate in groups[0].candidates] == [
        "ecc.review-code",
        "superpowers.code-review",
    ]
