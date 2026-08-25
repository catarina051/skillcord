from pathlib import Path


def test_public_release_requires_name_gate() -> None:
    checklist = Path("docs/release-checklist.md").read_text(encoding="utf-8")
    assert "PyPI namespace re-check" in checklist
    assert "semantic name collision review" in checklist
    assert "Skillcord is an internal codename" in checklist
