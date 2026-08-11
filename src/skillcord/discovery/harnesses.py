"""Hermetic detection of project-local agent harness configuration."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessDetection:
    """A project-local file or directory that indicates a harness configuration."""

    harness_id: str
    path: Path


class HarnessDetector:
    """Detect harnesses from supplied project-relative candidate paths only."""

    _DEFAULT_CANDIDATE_PATHS: Mapping[str, tuple[Path, ...]] = {
        "claude": (Path("CLAUDE.md"), Path(".claude")),
        "codex": (Path(".codex"),),
        "generic": (Path("AGENTS.md"),),
    }

    def __init__(self, candidate_paths: Mapping[str, Sequence[Path]] | None = None) -> None:
        paths = candidate_paths if candidate_paths is not None else self._DEFAULT_CANDIDATE_PATHS
        self._candidate_paths = {
            harness_id: tuple(candidates) for harness_id, candidates in paths.items()
        }

    def detect(self, project_root: Path) -> tuple[HarnessDetection, ...]:
        """Return one deterministic detection per harness with a present candidate."""

        resolved_root = project_root.resolve()
        detections: list[HarnessDetection] = []
        for harness_id in sorted(self._candidate_paths):
            candidate = next(
                (
                    path
                    for path in self._candidate_paths[harness_id]
                    if self._resolve_candidate(resolved_root, path).exists()
                ),
                None,
            )
            if candidate is not None:
                detections.append(
                    HarnessDetection(
                        harness_id=harness_id,
                        path=self._resolve_candidate(resolved_root, candidate),
                    )
                )
        return tuple(detections)

    @staticmethod
    def _resolve_candidate(project_root: Path, candidate: Path) -> Path:
        return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
