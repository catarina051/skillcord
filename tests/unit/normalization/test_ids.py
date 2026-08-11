"""Tests for deterministic normalized identifiers."""

from skillcord.normalization.ids import normalize_token


def test_normalize_token_is_deterministic() -> None:
    assert normalize_token("Test Driven Development") == "test-driven-development"
    assert normalize_token("test_driven-development") == "test-driven-development"
