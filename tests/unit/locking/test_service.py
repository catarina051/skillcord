"""Tests for deterministic locks built from resolved provider components."""

from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from skillcord.locking.service import LockService
from skillcord.models.lock import LockArtifact, LockComponent, LockFile, LockProvider
from skillcord.models.provider import ProviderComponent, ProviderSnapshot
from skillcord.models.status import CheckStatus


class _StaticRevisionResolver:
    def __init__(self, revision: str | None) -> None:
        self._revision = revision

    def resolve(self, path: Path) -> str | None:
        return self._revision


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


def test_lock_build_rejects_adapter_hash_that_does_not_match_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    artifact.write_bytes(b"actual content\n")
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                artifact_hashes={artifact: "0" * 64},
            )
        ],
    )

    with pytest.raises(ValueError, match="adapter hash mismatch"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_build_rejects_missing_artifact(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                artifact_hashes={missing: "0" * 64},
            )
        ],
    )

    with pytest.raises(ValueError, match="artifact missing"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_build_rejects_mutable_revision_label(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                source_revision="main",
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )

    with pytest.raises(ValueError, match="immutable 40-character Git commit SHA"):
        LockService(revision_resolver=_StaticRevisionResolver(None)).build(
            [snapshot], resolved_override_ids=set()
        )


def test_lock_serialize_rejects_mutable_revision_label(tmp_path: Path) -> None:
    lock = LockFile(
        schema_version=1,
        skillcord_schema_version=1,
        providers={
            "example": LockProvider(
                provider_id="example",
                components=[
                    LockComponent(
                        component_id="example",
                        source_path=tmp_path,
                        source_revision="main",
                    )
                ],
            )
        },
    )

    with pytest.raises(ValueError, match="immutable 40-character Git commit SHA"):
        LockService().serialize(lock)


def test_lock_build_rejects_changed_source_revision(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                source_revision="1" * 40,
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )

    with pytest.raises(ValueError, match="source revision changed before lock build"):
        LockService(revision_resolver=_StaticRevisionResolver("2" * 40)).build(
            [snapshot], resolved_override_ids=set()
        )


def test_lock_build_rejects_duplicate_component_id(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    component = ProviderComponent(
        component_id="example.duplicate",
        source_path=tmp_path,
        artifact_hashes={artifact: sha256(content).hexdigest()},
    )
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[component, component.model_copy()],
    )

    with pytest.raises(ValueError, match="duplicate component_id"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_build_rejects_canonical_artifact_aliases_stably(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.md"
    alias = tmp_path / "nested" / ".." / "artifact.md"
    content = b"content\n"
    artifact.write_bytes(content)
    content_hash = sha256(content).hexdigest()

    def build(artifact_hashes: dict[Path, str]) -> None:
        snapshot = ProviderSnapshot(
            provider_id="example",
            root_path=tmp_path,
            components=[
                ProviderComponent(
                    component_id="example",
                    source_path=tmp_path,
                    artifact_hashes=artifact_hashes,
                )
            ],
        )
        LockService().build([snapshot], resolved_override_ids=set())

    with pytest.raises(ValueError, match="canonical artifact path collision") as first:
        build({artifact: content_hash, alias: content_hash})
    with pytest.raises(ValueError, match="canonical artifact path collision") as second:
        build({alias: content_hash, artifact: content_hash})

    assert str(first.value) == str(second.value)


def test_lock_build_rejects_artifact_outside_component_directory(tmp_path: Path) -> None:
    component_root = tmp_path / "component"
    component_root.mkdir()
    outside = tmp_path / "outside.md"
    content = b"outside\n"
    outside.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=component_root,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=component_root,
                artifact_hashes={outside: sha256(content).hexdigest()},
            )
        ],
    )

    with pytest.raises(ValueError, match="outside component source"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_build_rejects_artifact_other_than_file_component(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    other = tmp_path / "other.md"
    source.write_bytes(b"source\n")
    other.write_bytes(b"other\n")
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example.file",
                source_path=source,
                artifact_hashes={other: sha256(b"other\n").hexdigest()},
            )
        ],
    )

    with pytest.raises(ValueError, match="outside component source"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_build_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    component_root = tmp_path / "component"
    outside_root = tmp_path / "outside"
    component_root.mkdir()
    outside_root.mkdir()
    outside = outside_root / "secret.md"
    content = b"secret\n"
    outside.write_bytes(content)
    link = component_root / "link"
    try:
        link.symlink_to(outside_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=component_root,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=component_root,
                artifact_hashes={link / "secret.md": sha256(content).hexdigest()},
            )
        ],
    )

    with pytest.raises(ValueError, match="outside component source"):
        LockService().build([snapshot], resolved_override_ids=set())


def test_lock_drift_reports_missing_artifact_explicitly(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )
    service = LockService(revision_resolver=_StaticRevisionResolver(None))
    lock = service.build([snapshot], resolved_override_ids=set())
    artifact.unlink()

    checks = service.check_drift(lock)

    assert checks[0].status is CheckStatus.FAIL
    assert checks[0].details["reason"] == "artifact_missing"


def test_lock_drift_reports_missing_file_component_explicitly(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example.file",
                source_path=artifact,
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )
    service = LockService(revision_resolver=_StaticRevisionResolver(None))
    lock = service.build([snapshot], resolved_override_ids=set())
    artifact.unlink()

    checks = service.check_drift(lock)

    assert checks[0].status is CheckStatus.FAIL
    assert checks[0].details["reason"] == "artifact_missing"


def test_lock_drift_rejects_artifact_outside_locked_component(tmp_path: Path) -> None:
    component_root = tmp_path / "component"
    component_root.mkdir()
    outside = tmp_path / "outside.md"
    content = b"outside\n"
    outside.write_bytes(content)
    lock = LockFile(
        schema_version=1,
        skillcord_schema_version=1,
        providers={
            "example": LockProvider(
                provider_id="example",
                components=[
                    LockComponent(
                        component_id="example",
                        source_path=component_root,
                        artifacts={
                            "outside": LockArtifact(
                                path=outside,
                                sha256=sha256(content).hexdigest(),
                            )
                        },
                    )
                ],
            )
        },
    )

    checks = LockService().check_drift(lock)

    assert checks[0].status is CheckStatus.FAIL
    assert checks[0].details["reason"] == "artifact_outside_component"


def test_lock_drift_reports_unavailable_locked_revision(tmp_path: Path) -> None:
    artifact = tmp_path / "SKILL.md"
    content = b"content\n"
    artifact.write_bytes(content)
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=tmp_path,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=tmp_path,
                source_revision="1" * 40,
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )
    service = LockService(revision_resolver=_StaticRevisionResolver(None))
    lock = service.build([snapshot], resolved_override_ids=set())

    checks = service.check_drift(lock)

    assert any(
        check.id == "lock.source_revision"
        and check.status is CheckStatus.FAIL
        and check.details["reason"] == "revision_unavailable"
        for check in checks
    )
