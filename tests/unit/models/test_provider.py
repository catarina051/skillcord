from pathlib import Path

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "provider\n- injected"),
        ("provider_id", "provider instructions"),
        ("skill_id", "skill`injected"),
        ("skill_id", "skill.md"),
    ],
)
def test_skill_record_rejects_noncanonical_identifiers(field: str, value: str) -> None:
    data = {
        "provider_id": "open_design",
        "skill_id": "ui-review-v2",
        "name": "UI Review",
        "source_path": Path("/tmp/SKILL.md"),
        "content_hash": "a" * 64,
    }
    data[field] = value

    with pytest.raises(ValidationError, match="canonical identifier"):
        SkillRecord.model_validate(data)


def test_skill_record_accepts_safe_hyphenated_numeric_and_underscore_ids() -> None:
    record = SkillRecord(
        provider_id="open_design",
        skill_id="ui-review_v2",
        name="UI Review",
        source_path=Path("/tmp/SKILL.md"),
        content_hash="a" * 64,
    )

    assert record.normalized_id == "open_design.ui-review_v2"
