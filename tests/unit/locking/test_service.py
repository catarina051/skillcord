"""Tests for deterministic locks built from resolved provider components."""

from hashlib import sha256
from pathlib import Path

import yaml

from skillcord.locking.service import LockService
from skillcord.models.provider import ProviderComponent, ProviderSnapshot


def test_lock_records_every_authoritative_provider_component(tmp_path: Path) -> None:
    provider_root = tmp_path / "provider"
    skill_path = provider_root / "skills" / "planning" / "SKILL.md"
    command_path = provider_root / "commands" / "review.md"
    skill_path.parent.mkdir(parents=True)
    command_path.parent.mkdir(parents=True)
    skill_bytes = b"planning artifact\n"
    command_bytes = b"review command\n"
    skill_path.write_bytes(skill_bytes)
    command_path.write_bytes(command_bytes)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=provider_root,
        components=[
            ProviderComponent(
                component_id="example.skills",
                source_path=provider_root,
                source_revision="1" * 40,
                updater_can_mutate=True,
                artifact_hashes={skill_path: sha256(skill_bytes).hexdigest()},
            ),
            ProviderComponent(
                component_id="example.command.review",
                source_path=command_path,
                updater_can_mutate=False,
                artifact_hashes={command_path: sha256(command_bytes).hexdigest()},
            ),
        ],
    )

    lock = LockService().build(
        [snapshot],
        resolved_override_ids={"example.planning", "example.review"},
    )

    provider = lock.providers["example"]
    assert provider.provider_id == "example"
    assert [component.component_id for component in provider.components] == [
        "example.command.review",
        "example.skills",
    ]
    command_component, skill_component = provider.components
    command_artifact = next(iter(command_component.artifacts.values()))
    skill_artifact = next(iter(skill_component.artifacts.values()))
    assert command_component.source_path == command_path.resolve()
    assert command_component.source_revision is None
    assert command_component.updater_can_mutate is False
    assert command_artifact.path == command_path.resolve()
    assert command_artifact.sha256 == sha256(command_bytes).hexdigest()
    assert skill_component.source_path == provider_root.resolve()
    assert skill_component.source_revision == "1" * 40
    assert skill_component.updater_can_mutate is True
    assert skill_artifact.path == skill_path.resolve()
    assert skill_artifact.sha256 == sha256(skill_bytes).hexdigest()
    assert lock.resolved_ids == {"example.planning", "example.review"}


def test_lock_yaml_is_byte_identical_for_equivalent_input_order(tmp_path: Path) -> None:
    provider_root = tmp_path / "provider"
    first_path = provider_root / "first.md"
    second_path = provider_root / "second.md"
    provider_root.mkdir()
    first_path.write_bytes(b"first\n")
    second_path.write_bytes(b"second\n")
    components = [
        ProviderComponent(
            component_id="example.second",
            source_path=provider_root,
            artifact_hashes={second_path: sha256(b"second\n").hexdigest()},
        ),
        ProviderComponent(
            component_id="example.first",
            source_path=provider_root,
            artifact_hashes={first_path: sha256(b"first\n").hexdigest()},
        ),
    ]
    first_snapshot = ProviderSnapshot(
        provider_id="example", root_path=provider_root, components=components
    )
    second_snapshot = ProviderSnapshot(
        provider_id="example", root_path=provider_root, components=list(reversed(components))
    )
    service = LockService()

    first = service.serialize(
        service.build([first_snapshot], resolved_override_ids={"example.z", "example.a"})
    )
    second = service.serialize(
        service.build([second_snapshot], resolved_override_ids={"example.a", "example.z"})
    )

    assert first == second
    assert first == service.serialize(service.build([first_snapshot], {"example.z", "example.a"}))
    assert yaml.safe_load(first)["resolved_ids"] == ["example.a", "example.z"]
