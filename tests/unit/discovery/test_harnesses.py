"""Tests for hermetic harness detection."""

from pathlib import Path

from skillcord.discovery.harnesses import HarnessDetector


def test_detector_uses_only_injected_candidate_paths(tmp_path: Path) -> None:
    """Host configuration must not affect discovery of project-local harness hints."""

    (tmp_path / "CLAUDE.md").write_text("project hint", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("project instructions", encoding="utf-8")
    detector = HarnessDetector(
        candidate_paths={
            "claude": (Path("CLAUDE.md"),),
            "codex": (Path(".codex"),),
            "generic": (Path("AGENTS.md"),),
        }
    )

    detections = detector.detect(tmp_path)

    assert [(item.harness_id, item.path) for item in detections] == [
        ("claude", (tmp_path / "CLAUDE.md").resolve()),
        ("generic", (tmp_path / "AGENTS.md").resolve()),
    ]
