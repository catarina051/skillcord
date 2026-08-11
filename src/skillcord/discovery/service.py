"""Read-only provider and harness discovery orchestration."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from skillcord.discovery.harnesses import HarnessDetection, HarnessDetector
from skillcord.discovery.sources import AdapterRegistry
from skillcord.models.provider import ProviderSnapshot
from skillcord.providers.base import ProviderDiscoveryError


@dataclass(frozen=True)
class DiscoveryWarning:
    """A non-fatal discovery condition that callers should present to users."""

    provider_id: str
    source_path: Path
    message: str


@dataclass(frozen=True)
class UnsupportedLayout:
    """An explicit source root that V1 refuses to parse or activate."""

    provider_id: str
    source_path: Path
    reason: str


@dataclass(frozen=True)
class DiscoveryResult:
    """Deterministic, normalized provider and project-harness inventory."""

    providers: tuple[ProviderSnapshot, ...]
    harnesses: tuple[HarnessDetection, ...]
    warnings: tuple[DiscoveryWarning, ...]
    unsupported_layouts: tuple[UnsupportedLayout, ...]

    @property
    def harness_detections(self) -> tuple[HarnessDetection, ...]:
        """Expose harnesses under the descriptive name used by callers."""

        return self.harnesses


class DiscoveryService:
    """Coordinate safe adapter selection without provider execution or mutation."""

    def __init__(self, registry: AdapterRegistry, harness_detector: HarnessDetector) -> None:
        self._registry = registry
        self._harness_detector = harness_detector

    @classmethod
    def default(cls, harness_detector: HarnessDetector | None = None) -> "DiscoveryService":
        """Build the deterministic V1 service with project-local harness candidates."""

        return cls(AdapterRegistry.default(), harness_detector or HarnessDetector())

    def scan(
        self, project_root: Path, source_roots: Mapping[str, Path]
    ) -> DiscoveryResult:
        """Discover explicit sources and project-local harnesses without side effects."""

        providers: list[ProviderSnapshot] = []
        warnings: list[DiscoveryWarning] = []
        unsupported_layouts: list[UnsupportedLayout] = []
        known_provider_ids = self._registry.known_provider_ids

        for provider_id, root in sorted(source_roots.items()):
            adapter = self._registry.adapter_for(provider_id, root)
            if adapter is None:
                unsupported_layouts.append(
                    UnsupportedLayout(
                        provider_id=provider_id,
                        source_path=root.resolve(),
                        reason="no supported adapter for explicit source root",
                    )
                )
                continue
            try:
                snapshot = adapter.discover(root)
            except ProviderDiscoveryError as error:
                unsupported_layouts.append(
                    UnsupportedLayout(
                        provider_id=provider_id,
                        source_path=error.path.resolve(),
                        reason=error.reason,
                    )
                )
                continue
            providers.append(snapshot)
            if provider_id not in known_provider_ids:
                warnings.append(
                    DiscoveryWarning(
                        provider_id=provider_id,
                        source_path=root.resolve(),
                        message="unknown provider parsed as an explicitly supplied generic SKILL.md root",
                    )
                )

        return DiscoveryResult(
            providers=tuple(providers),
            harnesses=self._harness_detector.detect(project_root),
            warnings=tuple(warnings),
            unsupported_layouts=tuple(unsupported_layouts),
        )
