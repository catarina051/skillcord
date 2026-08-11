"""Tests for raw-content hashing."""

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
