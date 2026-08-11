"""Hermetic detection of project-local agent harness configuration."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HarnessDetection:
    """A project-local file or directory that indicates a harness configuration."""

    harness_id: str
    path: Path


@dataclass(frozen=True)
class RejectedHarnessCandidate:
    """An injected hint refused before it could probe outside the project."""

    harness_id: str
    path: Path
    reason: str


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
                    resolved_candidate
                    for path in self._candidate_paths[harness_id]
                    if (resolved_candidate := self._project_relative_candidate(resolved_root, path))
                    is not None
                    and resolved_candidate.exists()
                ),
                None,
            )
            if candidate is not None:
                detections.append(
                    HarnessDetection(harness_id=harness_id, path=candidate)
                )
        return tuple(detections)

    def rejected_candidates(self, project_root: Path) -> tuple[RejectedHarnessCandidate, ...]:
        """Return invalid candidates without checking whether their targets exist."""

        resolved_root = project_root.resolve()
        rejected: list[RejectedHarnessCandidate] = []
        for harness_id in sorted(self._candidate_paths):
            for candidate in self._candidate_paths[harness_id]:
                reason = self._rejection_reason(resolved_root, candidate)
                if reason is not None:
                    rejected.append(
                        RejectedHarnessCandidate(
                            harness_id=harness_id,
                            path=candidate,
                            reason=reason,
                        )
                    )
        return tuple(rejected)

    @staticmethod
    def _project_relative_candidate(project_root: Path, candidate: Path) -> Path | None:
        if HarnessDetector._rejection_reason(project_root, candidate) is not None:
            return None
        return (project_root / candidate).resolve()

    @staticmethod
    def _rejection_reason(project_root: Path, candidate: Path) -> str | None:
        if candidate.is_absolute():
            return "absolute candidate path"
        resolved_candidate = (project_root / candidate).resolve()
        return None if resolved_candidate.is_relative_to(project_root) else "path escapes project root"
