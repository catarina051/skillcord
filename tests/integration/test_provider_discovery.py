"""Integration tests for deterministic provider discovery."""

from pathlib import Path

from skillcord.discovery.harnesses import HarnessDetector
from skillcord.discovery.service import DiscoveryService


def test_scan_uses_explicit_adapter_per_known_provider(tmp_path: Path) -> None:
    """Known roots must be routed through their provider-specific adapters."""

    result = DiscoveryService.default(
        harness_detector=HarnessDetector(candidate_paths={"generic": (Path("AGENTS.md"),)})
    ).scan(
        project_root=tmp_path,
        source_roots={
            "superpowers": Path("tests/fixtures/providers/superpowers"),
            "ecc": Path("tests/fixtures/providers/ecc"),
            "open_design": Path("tests/fixtures/providers/open_design"),
            "humanizer": Path("tests/fixtures/providers/humanizer"),
        },
    )

    assert [snapshot.provider_id for snapshot in result.providers] == [
        "ecc",
        "humanizer",
        "open_design",
        "superpowers",
    ]
    assert result.unsupported_layouts == ()


def test_scan_reports_malformed_or_unknown_layouts_without_discovering_them(tmp_path: Path) -> None:
    """A malformed explicit source must be visible but cannot be parsed generically."""

    malformed_root = tmp_path / "unknown"
    malformed_root.mkdir()
    (malformed_root / "README.md").write_text("not a skill", encoding="utf-8")

    result = DiscoveryService.default(
        harness_detector=HarnessDetector(candidate_paths={})
    ).scan(project_root=tmp_path, source_roots={"unrecognized": malformed_root})

    assert result.providers == ()
    assert [(item.provider_id, item.reason) for item in result.unsupported_layouts] == [
        ("unrecognized", "no supported adapter for explicit source root"),
    ]


def test_scan_warns_when_an_explicit_unknown_root_uses_generic_parsing(tmp_path: Path) -> None:
    """Callers must be told when V1 has no provider-specific layout support."""

    skill_root = tmp_path / "additional"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("---\nname: Additional\n---\n", encoding="utf-8")

    result = DiscoveryService.default(
        harness_detector=HarnessDetector(candidate_paths={})
    ).scan(project_root=tmp_path, source_roots={"additional": skill_root})

    assert [snapshot.provider_id for snapshot in result.providers] == ["additional"]
    assert [warning.message for warning in result.warnings] == [
        "unknown provider parsed as an explicitly supplied generic SKILL.md root"
    ]


def test_scan_reports_rejected_harness_candidates(tmp_path: Path) -> None:
    """Invalid harness hints must be visible without becoming detections."""

    absolute_hint = tmp_path.parent / "outside-absolute-report" / "codex.toml"
    absolute_hint.parent.mkdir()
    absolute_hint.write_text("host configuration", encoding="utf-8")
    detector = HarnessDetector(
        candidate_paths={
            "absolute": (absolute_hint,),
            "parent": (Path("..") / "outside-parent-report" / "codex.toml",),
        }
    )

    result = DiscoveryService.default(harness_detector=detector).scan(
        project_root=tmp_path, source_roots={}
    )

    assert result.harnesses == ()
    assert [(warning.provider_id, warning.source_path, warning.message) for warning in result.warnings] == [
        (None, absolute_hint, "harness candidate rejected: absolute candidate path"),
        (
            None,
            Path("..") / "outside-parent-report" / "codex.toml",
            "harness candidate rejected: path escapes project root",
        ),
    ]
