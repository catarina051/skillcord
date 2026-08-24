import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import skillcord.history.store as history_module
from skillcord.adapters.base import ArtifactOwnership
from skillcord.history.store import HistoryEntry, HistoryFormatError, HistoryStore
from skillcord.sync.applier import (
    StaleSyncPlanError,
    SyncApplier,
    SyncRollbackError,
)
from skillcord.sync.planner import PlannedFileChange, SyncPlan


def _entry() -> HistoryEntry:
    return HistoryEntry(
        timestamp="2026-08-11T17:00:00Z",
        action="sync",
        files_changed=["AGENTS.md"],
        provider_revisions={"superpowers": {"old": "a", "new": "b"}},
        deterministic_inverse=False,
    )


def _plan(
    root: Path,
    *names: str,
    ownership: ArtifactOwnership = "managed_block",
) -> SyncPlan:
    changes: list[PlannedFileChange] = []
    for name in names:
        target = root / name
        target.write_bytes(f"old {name}\n".encode())
        changes.append(
            PlannedFileChange(
                path=target,
                relative_path=Path(name),
                ownership=ownership,
                before=f"old {name}\n".encode(),
                after=f"new {name}\n".encode(),
            )
        )
    return SyncPlan(project_root=root, changes=tuple(changes))


def test_history_records_files_and_provider_revision(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    store.append(_entry())

    recorded = store.list()[0]
    assert recorded.action == "sync"
    assert recorded.files_changed == ["AGENTS.md"]
    assert recorded.provider_revisions["superpowers"].model_dump() == {
        "old": "a",
        "new": "b",
    }


def test_history_jsonl_is_deterministic_and_append_preserves_order(tmp_path: Path) -> None:
    path = tmp_path / ".ai" / "history.jsonl"
    store = HistoryStore(path)
    second = _entry().model_copy(
        update={"timestamp": "2026-08-11T18:00:00Z", "files_changed": ["CLAUDE.md"]}
    )

    store.append(_entry())
    store.append(second)

    assert path.read_text(encoding="utf-8") == (
        '{"action":"sync","deterministic_inverse":false,'
        '"files_changed":["AGENTS.md"],"provider_revisions":'
        '{"superpowers":{"new":"b","old":"a"}},'
        '"timestamp":"2026-08-11T17:00:00Z"}\n'
        '{"action":"sync","deterministic_inverse":false,'
        '"files_changed":["CLAUDE.md"],"provider_revisions":'
        '{"superpowers":{"new":"b","old":"a"}},'
        '"timestamp":"2026-08-11T18:00:00Z"}\n'
    )
    assert [entry.timestamp for entry in store.list()] == [
        "2026-08-11T17:00:00Z",
        "2026-08-11T18:00:00Z",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json\n",
        b"{}\n",
        b'{"action":"sync"}\n',
        b"\n",
        json.dumps(
            {
                "timestamp": "not-a-timestamp",
                "action": "sync",
                "files_changed": ["AGENTS.md"],
                "provider_revisions": {},
                "deterministic_inverse": False,
            }
        ).encode()
        + b"\n",
    ],
)
def test_history_list_fails_closed_on_malformed_entries(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "history.jsonl"
    path.write_bytes(payload)

    with pytest.raises(HistoryFormatError):
        HistoryStore(path).list()


def test_append_refuses_to_replace_malformed_history(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"not-json\n")

    with pytest.raises(HistoryFormatError):
        HistoryStore(path).append(_entry())

    assert path.read_bytes() == b"not-json\n"


def test_atomic_append_failure_preserves_existing_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path)
    store.append(_entry())
    before = path.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(history_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        store.append(
            _entry().model_copy(update={"timestamp": "2026-08-11T18:00:00Z"})
        )

    assert path.read_bytes() == before
    assert list(path.parent.glob("*.skillcord-history.tmp")) == []


def test_audit_append_failure_rolls_back_sync_and_is_reported_as_audit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path, "AGENTS.md", "CLAUDE.md")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    def fail_audit_append(_payload: bytes) -> None:
        raise OSError("injected audit replace failure")

    monkeypatch.setattr(store, "_atomic_replace", fail_audit_append)

    with pytest.raises(RuntimeError, match="audit"):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert (tmp_path / "AGENTS.md").read_bytes() == b"old AGENTS.md\n"
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"old CLAUDE.md\n"
    assert store.list() == []


def test_successful_sync_records_only_committed_managed_changes(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "AGENTS.md", "CLAUDE.md")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")
    fixed_now = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)

    result = SyncApplier(
        history_store=store,
        clock=lambda: fixed_now,
        provider_revisions={"superpowers": {"old": "a", "new": "b"}},
    ).apply(plan, approved=True)

    assert result.applied is True
    assert len(store.list()) == 1
    assert store.list()[0] == HistoryEntry(
        timestamp="2026-08-11T17:00:00Z",
        action="sync",
        files_changed=["AGENTS.md", "CLAUDE.md"],
        provider_revisions={"superpowers": {"old": "a", "new": "b"}},
        deterministic_inverse=True,
    )


def test_sync_does_not_claim_inverse_for_owned_file_changes(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "generated.json", ownership="owned_file")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    SyncApplier(
        history_store=store,
        clock=lambda: datetime(2026, 8, 11, 17, 0, tzinfo=UTC),
    ).apply(plan, approved=True)

    assert store.list()[0].deterministic_inverse is False


def test_rejected_sync_does_not_read_or_write_history(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "AGENTS.md")
    path = tmp_path / ".ai" / "history.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"malformed history\n")

    result = SyncApplier(history_store=HistoryStore(path)).apply(plan, approved=False)

    assert result.applied is False
    assert path.read_bytes() == b"malformed history\n"


def test_malformed_history_fails_before_sync_mutation(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "AGENTS.md")
    path = tmp_path / ".ai" / "history.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"malformed history\n")

    with pytest.raises(HistoryFormatError):
        SyncApplier(history_store=HistoryStore(path)).apply(plan, approved=True)

    assert (tmp_path / "AGENTS.md").read_bytes() == b"old AGENTS.md\n"
    assert path.read_bytes() == b"malformed history\n"


def test_preflight_failure_does_not_write_history(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "AGENTS.md")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")
    (tmp_path / "AGENTS.md").write_bytes(b"stale\n")

    with pytest.raises(StaleSyncPlanError):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert store.list() == []


def test_apply_failure_with_complete_rollback_does_not_write_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path, "a.md", "b.md")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    def fail_second(target: Path) -> None:
        if target.name == "b.md":
            raise OSError("injected apply failure")

    monkeypatch.setattr(SyncApplier, "_before_replace", staticmethod(fail_second))

    with pytest.raises(OSError, match="injected apply failure"):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert (tmp_path / "a.md").read_bytes() == b"old a.md\n"
    assert (tmp_path / "b.md").read_bytes() == b"old b.md\n"
    assert store.list() == []


def test_incomplete_rollback_does_not_write_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path, "a.md", "b.md")
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    def fail_second(target: Path) -> None:
        if target.name == "b.md":
            raise OSError("injected apply failure")

    monkeypatch.setattr(SyncApplier, "_before_replace", staticmethod(fail_second))
    monkeypatch.setattr(
        SyncApplier,
        "_rollback",
        staticmethod(lambda _applied: [OSError("injected rollback failure")]),
    )

    with pytest.raises(SyncRollbackError):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert store.list() == []


def test_noop_sync_does_not_create_history_entry(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")

    result = SyncApplier(history_store=store).apply(
        SyncPlan(project_root=tmp_path, changes=()),
        approved=True,
    )

    assert result.applied is True
    assert store.list() == []


def test_noop_sync_does_not_validate_or_modify_existing_history(tmp_path: Path) -> None:
    path = tmp_path / ".ai" / "history.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"malformed history remains untouched\n")

    result = SyncApplier(history_store=HistoryStore(path)).apply(
        SyncPlan(project_root=tmp_path, changes=()),
        approved=True,
    )

    assert result.applied is True
    assert result.changed_files == ()
    assert path.read_bytes() == b"malformed history remains untouched\n"
