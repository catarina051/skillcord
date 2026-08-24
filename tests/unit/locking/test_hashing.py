"""Tests for raw-content hashing."""

from hashlib import sha256
from pathlib import Path

from skillcord.locking.hashing import sha256_file


def test_sha256_file_is_content_based(tmp_path: Path) -> None:
    p = tmp_path / "SKILL.md"
    p.write_bytes(b"same-content\n")
    first = sha256_file(p)
    p.touch()
    second = sha256_file(p)
    assert first == second
    assert len(first) == 64


def test_sha256_file_hashes_raw_bytes(tmp_path: Path) -> None:
    raw_bytes = b"line one\r\nline two\xff\r\n"
    path = tmp_path / "SKILL.md"
    path.write_bytes(raw_bytes)

    assert sha256_file(path) == sha256(raw_bytes).hexdigest()
