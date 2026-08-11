"""Tests for persisted project capability decisions."""

import pytest
import yaml
from pydantic import ValidationError

from skillcord.decisions.store import DecisionStore
from skillcord.models.config import OverrideConfig


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


@pytest.mark.parametrize(
    "contents",
    [
        (
            "schema_version: 1\n"
            "overrides:\n"
            "  test-driven-development:\n"
            "    prefer: superpowers.test-driven-development\n"
            "  test-driven-development:\n"
            "    suppress: [ecc.tdd]\n"
        ),
        (
            "schema_version: 1\n"
            "overrides:\n"
            "  test-driven-development:\n"
            "    prefer: superpowers.test-driven-development\n"
            "    prefer: ecc.tdd\n"
        ),
    ],
)
def test_save_decision_rejects_duplicate_yaml_keys_without_rewriting_file(tmp_path, contents) -> None:
    """Ambiguous YAML must not collapse to a last-value-wins decision."""

    path = tmp_path / ".ai" / "overrides.yaml"
    path.parent.mkdir()
    path.write_text(contents, encoding="utf-8")
    store = DecisionStore(path)

    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        store.save_decision(
            capability_id="security-review",
            prefer="ecc.security-review",
            suppress=[],
        )

    assert path.read_text(encoding="utf-8") == contents


@pytest.mark.parametrize(
    "contents",
    [
        "schema_version: 2\noverrides: {}\n",
        (
            "schema_version: 1\n"
            "overrides:\n"
            "  test-driven-development:\n"
            "    prefer: [not-a-skill-id]\n"
        ),
    ],
)
def test_save_validates_existing_file_before_replacing_it(tmp_path, contents) -> None:
    """A direct public save cannot erase an invalid persisted decision file."""

    path = tmp_path / ".ai" / "overrides.yaml"
    path.parent.mkdir()
    path.write_text(contents, encoding="utf-8")
    store = DecisionStore(path)

    with pytest.raises(ValidationError):
        store.save(OverrideConfig(schema_version=1))

    assert path.read_text(encoding="utf-8") == contents
