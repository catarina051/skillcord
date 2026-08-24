"""Content-addressing helpers for immutable provider artifacts."""

from hashlib import sha256
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file's raw bytes."""

    digest = sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
