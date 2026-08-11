from pathlib import Path

from skillcord.models.provider import SkillRecord


def test_skill_record_has_stable_provider_scoped_id() -> None:
    record = SkillRecord(
        provider_id="superpowers",
        skill_id="test-driven-development",
        name="Test Driven Development",
        description="Write the test first.",
        source_path=Path("/tmp/SKILL.md"),
        content_hash="a" * 64,
        capabilities={"tdd"},
        harnesses={"claude", "codex"},
    )
    assert record.normalized_id == "superpowers.test-driven-development"
