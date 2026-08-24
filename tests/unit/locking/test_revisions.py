"""Tests for immutable, read-only Git revision resolution."""

import subprocess
from pathlib import Path

import pytest

from skillcord.locking.revisions import GitRevisionResolver


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a real isolated repository with one immutable commit."""

    repository = tmp_path / "provider-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    subprocess.run(
        ["git", "config", "user.email", "skillcord-tests@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Skillcord Tests"], cwd=repository, check=True
    )
    (repository / "SKILL.md").write_text("provider content\n", encoding="utf-8")
    subprocess.run(["git", "add", "SKILL.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repository, check=True)
    return repository


def test_git_revision_resolver_returns_commit_sha(tmp_git_repo: Path) -> None:
    revision = GitRevisionResolver().resolve(tmp_git_repo)

    assert revision is not None
    assert len(revision) == 40
    assert all(character in "0123456789abcdef" for character in revision)


def test_git_revision_resolver_returns_none_outside_git_repository(tmp_path: Path) -> None:
    source = tmp_path / "plain-provider"
    source.mkdir()

    assert GitRevisionResolver().resolve(source) is None


def test_git_revision_resolver_supports_linked_worktree(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    linked_worktree = tmp_path / "linked-provider"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "-b", "linked-provider", str(linked_worktree)],
        cwd=tmp_git_repo,
        check=True,
    )

    root_revision = GitRevisionResolver().resolve(tmp_git_repo)

    assert root_revision is not None
    assert GitRevisionResolver().resolve(linked_worktree) == root_revision
