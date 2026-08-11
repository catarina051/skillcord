"""Tests for persisted project capability decisions."""

from skillcord.decisions.store import DecisionStore


def test_override_round_trip(tmp_path) -> None:
    """A saved ownership choice is available to later resolution runs."""

    path = tmp_path / ".ai" / "overrides.yaml"
    store = DecisionStore(path)

    store.save_decision(
        capability_id="test-driven-development",
        prefer="superpowers.test-driven-development",
        suppress=["ecc.tdd"],
    )

    loaded = store.load()

    assert loaded.overrides["test-driven-development"].prefer == (
        "superpowers.test-driven-development"
    )
    assert loaded.overrides["test-driven-development"].suppress == ["ecc.tdd"]


def test_save_orders_capabilities_and_suppressed_skill_ids_stably(tmp_path) -> None:
    """Equivalent decisions produce reviewable, deterministic YAML."""

    path = tmp_path / ".ai" / "overrides.yaml"
    store = DecisionStore(path)
    store.save_decision(
        capability_id="zebra",
        prefer=None,
        suppress=["provider.z", "provider.a"],
    )
    store.save_decision(
        capability_id="alpha",
        prefer="provider.alpha",
        suppress=[],
    )

    assert path.read_text(encoding="utf-8") == (
        "schema_version: 1\n"
        "overrides:\n"
        "  alpha:\n"
        "    prefer: provider.alpha\n"
        "    suppress: []\n"
        "  zebra:\n"
        "    prefer: null\n"
        "    suppress:\n"
        "    - provider.a\n"
        "    - provider.z\n"
    )
