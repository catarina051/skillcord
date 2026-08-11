"""Deterministic lock generation and applied-artifact drift checks."""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from skillcord.locking.hashing import sha256_file
from skillcord.models.lock import LockArtifact, LockComponent, LockFile, LockProvider
from skillcord.models.provider import ProviderComponent, ProviderSnapshot
from skillcord.models.status import CheckResult, CheckStatus


class LockService:
    """Pin the exact components supplied by provider discovery."""

    def build(
        self,
        snapshot_set: Iterable[ProviderSnapshot],
        resolved_override_ids: Iterable[str],
    ) -> LockFile:
        """Build a lock from authoritative provider component snapshots."""

        providers: dict[str, LockProvider] = {}
        for snapshot in sorted(snapshot_set, key=lambda item: item.provider_id):
            if snapshot.provider_id in providers:
                raise ValueError(f"duplicate provider snapshot: {snapshot.provider_id}")
            providers[snapshot.provider_id] = LockProvider(
                provider_id=snapshot.provider_id,
                components=[
                    self._lock_component(component)
                    for component in sorted(
                        snapshot.components,
                        key=lambda item: (item.component_id, item.source_path.resolve().as_posix()),
                    )
                ],
            )
        return LockFile(
            schema_version=1,
            skillcord_schema_version=1,
            providers=providers,
            resolved_ids=set(resolved_override_ids),
        )

    def serialize(self, lock: LockFile) -> bytes:
        """Serialize a lock to stable UTF-8 YAML bytes."""

        payload = self._canonicalize(lock.model_dump(mode="python"))
        serialized = cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=True))
        return serialized.encode("utf-8")

    def check_drift(self, lock: LockFile) -> list[CheckResult]:
        """Compare every locked artifact with its current raw-content hash."""

        checks: list[CheckResult] = []
        for provider_id, provider in sorted(lock.providers.items()):
            for component in provider.components:
                for artifact in component.artifacts.values():
                    details: dict[str, object] = {
                        "provider_id": provider_id,
                        "component_id": component.component_id,
                        "path": str(artifact.path),
                        "expected_sha256": artifact.sha256,
                    }
                    try:
                        actual_hash = sha256_file(artifact.path)
                    except OSError as error:
                        details.update(
                            {
                                "actual_sha256": None,
                                "reason": "artifact_unreadable",
                                "error": type(error).__name__,
                            }
                        )
                        status = CheckStatus.FAIL
                    else:
                        details["actual_sha256"] = actual_hash
                        status = (
                            CheckStatus.PASS
                            if actual_hash == artifact.sha256
                            else CheckStatus.FAIL
                        )
                        if status is CheckStatus.FAIL:
                            details["reason"] = "hash_mismatch"
                    checks.append(
                        CheckResult(id="lock.artifact_hash", status=status, details=details)
                    )
        return checks

    def _lock_component(self, component: ProviderComponent) -> LockComponent:
        source_path = component.source_path.resolve()
        artifacts: dict[str, LockArtifact] = {}
        for path, content_hash in sorted(
            component.artifact_hashes.items(), key=lambda item: item[0].resolve().as_posix()
        ):
            resolved_path = path.resolve()
            artifacts[resolved_path.as_posix()] = LockArtifact(
                path=resolved_path,
                sha256=content_hash,
            )
        return LockComponent(
            component_id=component.component_id,
            source_path=source_path,
            source_revision=component.source_revision,
            updater_can_mutate=component.updater_can_mutate,
            artifacts=artifacts,
        )

    def _canonicalize(self, value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, Mapping):
            return {
                str(key): self._canonicalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, AbstractSet) and not isinstance(value, (str, bytes)):
            return [self._canonicalize(item) for item in sorted(value, key=str)]
        if isinstance(value, list):
            return [self._canonicalize(item) for item in value]
        return value
