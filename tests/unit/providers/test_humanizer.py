"""Tests for the read-only, harness-only Humanizer adapter."""

from pathlib import Path

from skillcord.providers.humanizer import HumanizerAdapter


def test_humanizer_is_harness_only() -> None:
    snapshot = HumanizerAdapter().discover(Path("tests/fixtures/providers/humanizer"))

    assert snapshot.skills[0].normalized_id == "humanizer.humanizer"
    assert snapshot.skills[0].execution_mode == "harness_only"
    assert snapshot.skills[0].optional is True
