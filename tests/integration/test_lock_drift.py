"""Integration coverage for provider-managed artifact drift."""

import subprocess
from hashlib import sha256
from pathlib import Path
from shutil import copytree

from skillcord.locking.service import LockService
from skillcord.models.provider import ProviderComponent, ProviderSnapshot
from skillcord.models.status import CheckStatus
from skillcord.providers.superpowers import SuperpowersAdapter


def test_modified_locked_artifact_fails_explicit_hash_check(tmp_path: Path) -> None:
    provider_root = copytree(
        Path("tests/fixtures/providers/superpowers"), tmp_path / "superpowers"
    )
    snapshot = SuperpowersAdapter().discover(provider_root)
    service = LockService()
    lock = service.build([snapshot], resolved_override_ids={"superpowers.brainstorming"})
    locked_provider = lock.providers["superpowers"]
    locked_component = locked_provider.components[0]
    artifact_path = provider_root / "skills" / "brainstorming" / "SKILL.md"

    artifact_path.write_text("provider-managed update\n", encoding="utf-8")
    checks = service.check_drift(lock)

    assert locked_provider.provider_id == "superpowers"
    assert locked_component.source_path == provider_root.resolve()
    assert any(
        check.id == "lock.artifact_hash"
        and check.status is CheckStatus.FAIL
        and check.details["path"] == str(artifact_path.resolve())
        for check in checks
    )


def test_new_git_commit_fails_locked_source_revision_check(tmp_path: Path) -> None:
    provider_root = tmp_path / "git-provider"
    provider_root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(provider_root)], check=True)
    subprocess.run(
        ["git", "config", "user.email", "skillcord-tests@example.invalid"],
        cwd=provider_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Skillcord Tests"],
        cwd=provider_root,
        check=True,
    )
    artifact = provider_root / "SKILL.md"
    revision_marker = provider_root / "REVISION.txt"
    content = b"locked artifact\n"
    artifact.write_bytes(content)
    revision_marker.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "SKILL.md", "REVISION.txt"], cwd=provider_root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "initial"], cwd=provider_root, check=True
    )
    snapshot = ProviderSnapshot(
        provider_id="example",
        root_path=provider_root,
        components=[
            ProviderComponent(
                component_id="example",
                source_path=provider_root,
                artifact_hashes={artifact: sha256(content).hexdigest()},
            )
        ],
    )
    service = LockService()
    lock = service.build([snapshot], resolved_override_ids=set())
    locked_revision = lock.providers["example"].components[0].source_revision

    revision_marker.write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "REVISION.txt"], cwd=provider_root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "provider update"],
        cwd=provider_root,
        check=True,
    )
    checks = service.check_drift(lock)

    assert locked_revision is not None
    assert any(
        check.id == "lock.source_revision"
        and check.status is CheckStatus.FAIL
        and check.details["reason"] == "revision_mismatch"
        for check in checks
    )
    assert any(
        check.id == "lock.artifact_hash" and check.status is CheckStatus.PASS
        for check in checks
    )
