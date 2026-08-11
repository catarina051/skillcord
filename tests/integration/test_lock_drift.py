"""Integration coverage for provider-managed artifact drift."""

from pathlib import Path
from shutil import copytree

from skillcord.locking.service import LockService
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
