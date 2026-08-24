"""Read-only immutable revision resolution for local provider components."""

import re
import subprocess
from pathlib import Path
from typing import Protocol


class RevisionResolver(Protocol):
    """Resolve an optional immutable revision for a local source component."""

    def resolve(self, path: Path) -> str | None:
        """Return an immutable revision when the source exposes one."""


class GitRevisionResolver:
    """Resolve the commit containing a path using read-only Git metadata."""

    def resolve(self, path: Path) -> str | None:
        """Return the current commit SHA, or ``None`` when Git cannot resolve it."""

        source_directory = path if path.is_dir() else path.parent
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-optional-locks",
                    "-C",
                    str(source_directory),
                    "rev-parse",
                    "--verify",
                    "HEAD^{commit}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return None

        revision = result.stdout.strip().lower()
        if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            return None
        return revision
