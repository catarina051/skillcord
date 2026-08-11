"""Deterministic lock generation and applied-artifact drift checks."""

import re
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from skillcord.locking.hashing import sha256_file
from skillcord.locking.revisions import GitRevisionResolver, RevisionResolver
from skillcord.models.lock import LockArtifact, LockComponent, LockFile, LockProvider
from skillcord.models.provider import ProviderComponent, ProviderSnapshot
from skillcord.models.status import CheckResult, CheckStatus


class LockService:
    """Pin the exact components supplied by provider discovery."""

    def __init__(self, revision_resolver: RevisionResolver | None = None) -> None:
        self._revision_resolver = revision_resolver or GitRevisionResolver()

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
            components = sorted(
                snapshot.components,
                key=lambda item: (item.component_id, item.source_path.resolve().as_posix()),
            )
            duplicate_component_ids = sorted(
                {
                    component.component_id
                    for component in components
                    if sum(
                        candidate.component_id == component.component_id
                        for candidate in components
                    )
                    > 1
                }
            )
            if duplicate_component_ids:
                raise ValueError(
                    f"{snapshot.provider_id}: duplicate component_id: "
                    f"{', '.join(duplicate_component_ids)}"
                )
            providers[snapshot.provider_id] = LockProvider(
                provider_id=snapshot.provider_id,
                components=[self._lock_component(snapshot.provider_id, component) for component in components],
            )
        return LockFile(
            schema_version=1,
            skillcord_schema_version=1,
            providers=providers,
            resolved_ids=set(resolved_override_ids),
        )

    def serialize(self, lock: LockFile) -> bytes:
        """Serialize a lock to stable UTF-8 YAML bytes."""

        for provider in lock.providers.values():
            for component in provider.components:
                self._validate_revision(
                    component.source_revision,
                    context=f"{provider.provider_id}/{component.component_id}",
                )
        payload = self._canonicalize(lock.model_dump(mode="python"))
        serialized = cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=True))
        return serialized.encode("utf-8")

    def check_drift(self, lock: LockFile) -> list[CheckResult]:
        """Compare every locked artifact with its current raw-content hash."""

        checks: list[CheckResult] = []
        for provider_id, provider in sorted(lock.providers.items()):
            for component in sorted(
                provider.components,
                key=lambda item: (item.component_id, item.source_path.resolve().as_posix()),
            ):
                checks.extend(self._check_revision(provider_id, component))
                source_path = component.source_path.resolve()
                for _, artifact in sorted(component.artifacts.items()):
                    artifact_path = artifact.path.resolve()
                    details: dict[str, object] = {
                        "provider_id": provider_id,
                        "component_id": component.component_id,
                        "path": str(artifact_path),
                        "expected_sha256": artifact.sha256,
                    }
                    if not self._artifact_is_authorized(source_path, artifact_path):
                        details.update(
                            {
                                "actual_sha256": None,
                                "reason": "artifact_outside_component",
                            }
                        )
                        checks.append(
                            CheckResult(
                                id="lock.artifact_hash",
                                status=CheckStatus.FAIL,
                                details=details,
                            )
                        )
                        continue
                    try:
                        actual_hash = sha256_file(artifact_path)
                    except FileNotFoundError:
                        details.update(
                            {
                                "actual_sha256": None,
                                "reason": "artifact_missing",
                            }
                        )
                        status = CheckStatus.FAIL
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

    def _lock_component(
        self, provider_id: str, component: ProviderComponent
    ) -> LockComponent:
        source_path = component.source_path.resolve()
        context = f"{provider_id}/{component.component_id}"
        declared_revision = self._validate_revision(
            component.source_revision,
            context=context,
        )
        current_revision = self._validate_revision(
            self._revision_resolver.resolve(source_path),
            context=f"{context} resolved",
        )
        if (
            declared_revision is not None
            and current_revision is not None
            and declared_revision != current_revision
        ):
            raise ValueError(
                f"{context}: source revision changed before lock build: "
                f"declared {declared_revision}, current {current_revision}"
            )
        source_revision = current_revision or declared_revision

        resolved_artifacts: list[tuple[str, Path, Path, str]] = []
        for path, supplied_hash in component.artifact_hashes.items():
            resolved_artifacts.append(
                (path.as_posix(), path, path.resolve(), supplied_hash)
            )
        resolved_artifacts.sort(key=lambda item: (item[2].as_posix(), item[0]))
        for index, first in enumerate(resolved_artifacts):
            aliases = [
                item[0]
                for item in resolved_artifacts[index:]
                if item[2] == first[2]
            ]
            if len(aliases) > 1:
                raise ValueError(
                    f"{context}: canonical artifact path collision at {first[2]}: "
                    f"{', '.join(sorted(aliases))}"
                )

        artifacts: dict[str, LockArtifact] = {}
        for _, _, resolved_path, supplied_hash in resolved_artifacts:
            if not self._artifact_is_authorized(source_path, resolved_path):
                raise ValueError(
                    f"{context}: artifact outside component source: {resolved_path}"
                )
            try:
                actual_hash = sha256_file(resolved_path)
            except FileNotFoundError as error:
                raise ValueError(f"{context}: artifact missing: {resolved_path}") from error
            except OSError as error:
                raise ValueError(
                    f"{context}: artifact unreadable: {resolved_path}: {type(error).__name__}"
                ) from error
            if actual_hash != supplied_hash:
                raise ValueError(
                    f"{context}: adapter hash mismatch for {resolved_path}: "
                    f"supplied {supplied_hash}, actual {actual_hash}"
                )
            artifacts[resolved_path.as_posix()] = LockArtifact(
                path=resolved_path,
                sha256=actual_hash,
            )
        return LockComponent(
            component_id=component.component_id,
            source_path=source_path,
            source_revision=source_revision,
            updater_can_mutate=component.updater_can_mutate,
            artifacts=artifacts,
        )

    def _check_revision(
        self, provider_id: str, component: LockComponent
    ) -> list[CheckResult]:
        if component.source_revision is None:
            return []
        details: dict[str, object] = {
            "provider_id": provider_id,
            "component_id": component.component_id,
            "path": str(component.source_path.resolve()),
            "expected_revision": component.source_revision,
        }
        try:
            expected_revision = self._validate_revision(
                component.source_revision,
                context=f"{provider_id}/{component.component_id}",
            )
        except ValueError:
            details.update({"actual_revision": None, "reason": "revision_invalid"})
            return [
                CheckResult(
                    id="lock.source_revision",
                    status=CheckStatus.FAIL,
                    details=details,
                )
            ]

        current_revision = self._revision_resolver.resolve(component.source_path.resolve())
        if current_revision is None or re.fullmatch(r"[0-9a-fA-F]{40}", current_revision) is None:
            details.update(
                {
                    "actual_revision": current_revision,
                    "reason": "revision_unavailable",
                }
            )
            status = CheckStatus.FAIL
        else:
            current_revision = current_revision.lower()
            details["actual_revision"] = current_revision
            status = (
                CheckStatus.PASS
                if current_revision == expected_revision
                else CheckStatus.FAIL
            )
            if status is CheckStatus.FAIL:
                details["reason"] = "revision_mismatch"
        return [
            CheckResult(
                id="lock.source_revision",
                status=status,
                details=details,
            )
        ]

    def _validate_revision(self, revision: str | None, *, context: str) -> str | None:
        if revision is None:
            return None
        if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
            raise ValueError(
                f"{context}: source_revision must be an immutable 40-character Git commit SHA"
            )
        return revision.lower()

    def _artifact_is_authorized(self, source_path: Path, artifact_path: Path) -> bool:
        if artifact_path == source_path:
            return not source_path.is_dir()
        if source_path.is_file():
            return False
        return artifact_path.is_relative_to(source_path)

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
