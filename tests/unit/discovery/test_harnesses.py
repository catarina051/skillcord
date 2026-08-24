"""Tests for hermetic harness detection."""

from pathlib import Path

import pytest

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


def test_detector_rejects_an_absolute_candidate_outside_project_root(tmp_path: Path) -> None:
    """An injected absolute host path must not be probed as a harness hint."""

    outside_hint = tmp_path.parent / "outside-absolute" / "codex.toml"
    outside_hint.parent.mkdir()
    outside_hint.write_text("host configuration", encoding="utf-8")
    detector = HarnessDetector(candidate_paths={"codex": (outside_hint,)})

    assert detector.detect(tmp_path) == ()


def test_detector_does_not_probe_rejected_absolute_candidate(tmp_path: Path, monkeypatch) -> None:
    """Rejection occurs before any target-existence check on the host path."""

    absolute_hint = tmp_path.parent / "outside-unprobed" / "codex.toml"
    detector = HarnessDetector(candidate_paths={"codex": (absolute_hint,)})
    original_exists = Path.exists

    def fail_on_host_probe(path: Path) -> bool:
        if path == absolute_hint:
            raise AssertionError("absolute host hint must not be probed")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fail_on_host_probe)

    assert detector.detect(tmp_path) == ()


def test_detector_rejects_parent_relative_candidate_outside_project_root(tmp_path: Path) -> None:
    """A parent-relative candidate must not escape the supplied project root."""

    outside_hint = tmp_path.parent / "outside-parent" / "codex.toml"
    outside_hint.parent.mkdir()
    outside_hint.write_text("host configuration", encoding="utf-8")
    detector = HarnessDetector(
        candidate_paths={"codex": (Path("..") / "outside-parent" / "codex.toml",)}
    )

    assert detector.detect(tmp_path) == ()


def test_detector_rejects_symlink_candidate_that_escapes_project_root(tmp_path: Path) -> None:
    """Resolving a project-local symlink must not permit a host-path probe."""

    outside_root = tmp_path.parent / "outside-symlink"
    outside_root.mkdir()
    (outside_root / "codex.toml").write_text("host configuration", encoding="utf-8")
    symlink = tmp_path / "linked"
    try:
        symlink.symlink_to(outside_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable on this test host: {error}")
    detector = HarnessDetector(candidate_paths={"codex": (Path("linked/codex.toml"),)})

    assert detector.detect(tmp_path) == ()
