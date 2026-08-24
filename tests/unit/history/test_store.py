import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

import skillcord.history.store as history_module
from skillcord.adapters.base import ArtifactOwnership
from skillcord.history.store import HistoryEntry, HistoryFormatError, HistoryStore
from skillcord.sync.applier import (
    StaleSyncPlanError,
    SyncApplier,
    SyncAuditCompensationError,
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


class _FirstReadBarrierStore(HistoryStore):
    def __init__(self, path: Path, ready: Path, peer_ready: Path) -> None:
        super().__init__(path)
        self._ready = ready
        self._peer_ready = peer_ready
        self._read_count = 0

    def _read_entries(self) -> list[HistoryEntry]:
        entries = super()._read_entries()
        self._read_count += 1
        if self._read_count == 1:
            self._ready.touch()
            deadline = time.monotonic() + 10
            while not self._peer_ready.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("peer history writer did not reach first read")
                time.sleep(0.01)
        return entries


def _append_history_in_process(
    path: Path,
    ready: Path,
    peer_ready: Path,
    timestamp: str,
    changed_file: str,
) -> None:
    store = _FirstReadBarrierStore(path, ready, peer_ready)
    store.append(
        _entry().model_copy(
            update={"timestamp": timestamp, "files_changed": [changed_file]}
        )
    )


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


def test_separate_store_instances_preserve_concurrent_appends(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    barrier = Barrier(2)

    class FirstReadBarrierStore(HistoryStore):
        def __init__(self) -> None:
            super().__init__(path)
            self._read_count = 0

        def _read_entries(self) -> list[HistoryEntry]:
            entries = super()._read_entries()
            self._read_count += 1
            if self._read_count == 1:
                barrier.wait(timeout=10)
            return entries

    first = FirstReadBarrierStore()
    second = FirstReadBarrierStore()
    entries = (
        _entry(),
        _entry().model_copy(
            update={"timestamp": "2026-08-11T18:00:00Z", "files_changed": ["CLAUDE.md"]}
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.append, entry)
            for store, entry in zip((first, second), entries, strict=True)
        ]
        for future in futures:
            future.result(timeout=10)

    assert sorted(entry.timestamp for entry in HistoryStore(path).list()) == [
        "2026-08-11T17:00:00Z",
        "2026-08-11T18:00:00Z",
    ]


def test_separate_processes_preserve_concurrent_appends(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    first_ready = tmp_path / "first.ready"
    second_ready = tmp_path / "second.ready"
    context = multiprocessing.get_context("spawn")
    processes = (
        context.Process(
            target=_append_history_in_process,
            args=(
                path,
                first_ready,
                second_ready,
                "2026-08-11T17:00:00Z",
                "AGENTS.md",
            ),
        ),
        context.Process(
            target=_append_history_in_process,
            args=(
                path,
                second_ready,
                first_ready,
                "2026-08-11T18:00:00Z",
                "CLAUDE.md",
            ),
        ),
    )

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert not process.is_alive()
        assert process.exitcode == 0

    assert sorted(entry.timestamp for entry in HistoryStore(path).list()) == [
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


@pytest.mark.parametrize(
    "payload",
    [
        (
            b'{"timestamp":"2026-08-11T17:00:00Z","action":"sync",'
            b'"action":"update","files_changed":["AGENTS.md"],'
            b'"provider_revisions":{},"deterministic_inverse":false}\n'
        ),
        (
            b'{"timestamp":"2026-08-11T17:00:00Z","action":"sync",'
            b'"files_changed":["AGENTS.md"],"provider_revisions":'
            b'{"superpowers":{"old":"a","old":"b","new":"c"}},'
            b'"deterministic_inverse":false}\n'
        ),
    ],
)
def test_history_rejects_duplicate_json_keys_at_every_depth(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "history.jsonl"
    path.write_bytes(payload)

    with pytest.raises(HistoryFormatError, match="malformed"):
        HistoryStore(path).list()


def test_history_rejects_numeric_boolean_coercion() -> None:
    with pytest.raises(ValidationError):
        HistoryEntry(
            timestamp="2026-08-11T17:00:00Z",
            action="sync",
            files_changed=["AGENTS.md"],
            provider_revisions={},
            deterministic_inverse=1,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "path",
    [
        "C:/secret",
        "C:secret",
        "./AGENTS.md",
        "a//b",
        " AGENTS.md",
        "AGENTS.md ",
        "folder name/AGENTS.md",
        "AGENTS\n.md",
        "AGENTS\u202e.md",
    ],
)
def test_history_rejects_noncanonical_or_unsafe_paths(path: str) -> None:
    payload = _entry().model_dump(mode="python")
    payload["files_changed"] = [path]

    with pytest.raises(ValidationError):
        HistoryEntry.model_validate(payload)


@pytest.mark.parametrize(
    "provider_id",
    ["UPPER", "bad/provider", " bad", "bad id", "bad\u0000id"],
)
def test_history_rejects_invalid_provider_ids(provider_id: str) -> None:
    payload = _entry().model_dump(mode="python")
    payload["provider_revisions"] = {provider_id: {"old": "a", "new": "b"}}

    with pytest.raises(ValidationError):
        HistoryEntry.model_validate(payload)


@pytest.mark.parametrize("revision", ["", " ", "\t"])
def test_history_rejects_blank_provider_revisions(revision: str) -> None:
    payload = _entry().model_dump(mode="python")
    payload["provider_revisions"] = {
        "superpowers": {"old": revision, "new": "b"}
    }

    with pytest.raises(ValidationError):
        HistoryEntry.model_validate(payload)


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


def test_interprocess_lock_closes_descriptor_when_acquisition_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[int] = []
    closed: list[int] = []
    real_open = history_module.os.open
    real_close = history_module.os.close
    real_load_symbol = history_module._load_symbol
    lock_symbol = "locking" if history_module.os.name == "nt" else "flock"

    def tracking_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(descriptor)
        return descriptor

    def tracking_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    def load_symbol(module_name: str, symbol_name: str) -> object:
        if symbol_name == lock_symbol:
            def fail_lock(*_args: object) -> None:
                raise OSError("injected lock acquisition failure")

            return fail_lock
        return real_load_symbol(module_name, symbol_name)

    monkeypatch.setattr(history_module.os, "open", tracking_open)
    monkeypatch.setattr(history_module.os, "close", tracking_close)
    monkeypatch.setattr(history_module, "_load_symbol", load_symbol)

    try:
        with (
            pytest.raises(OSError, match="injected lock acquisition failure"),
            history_module._InterprocessLock(tmp_path / "history.lock"),
        ):
            pass
    finally:
        for descriptor in opened:
            if descriptor not in closed:
                real_close(descriptor)

    assert closed == opened


def test_append_refuses_a_symbolic_link_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "history.jsonl"
    lock_path = tmp_path / ".history.jsonl.lock"
    real_is_symlink = Path.is_symlink

    def reported_symlink(candidate: Path) -> bool:
        return candidate == lock_path or real_is_symlink(candidate)

    monkeypatch.setattr(Path, "is_symlink", reported_symlink)

    with pytest.raises(HistoryFormatError, match="lock path"):
        HistoryStore(path).append(_entry())

    assert not path.exists()


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


def test_append_that_commits_then_raises_does_not_leave_orphan_history(
    tmp_path: Path,
) -> None:
    class CommitThenRaiseStore(HistoryStore):
        def _atomic_replace(self, payload: bytes) -> None:
            super()._atomic_replace(payload)
            raise OSError("injected failure after history commit")

    plan = _plan(tmp_path, "AGENTS.md")
    store = CommitThenRaiseStore(tmp_path / ".ai" / "history.jsonl")

    with pytest.raises(RuntimeError, match="audit"):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert (tmp_path / "AGENTS.md").read_bytes() == b"old AGENTS.md\n"
    assert store.list() == []


def test_precommit_append_failure_preserves_an_independent_equal_entry(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".ai" / "history.jsonl"

    class AppendEqualThenRaiseBeforeCommitStore(HistoryStore):
        def append(self, entry: HistoryEntry) -> None:
            HistoryStore(self.path).append(entry)
            super().append(entry)

        def _atomic_replace(self, _payload: bytes) -> None:
            raise OSError("injected failure before this store commits")

    plan = _plan(tmp_path, "AGENTS.md")
    fixed_now = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)
    store = AppendEqualThenRaiseBeforeCommitStore(path)

    with pytest.raises(RuntimeError, match="audit"):
        SyncApplier(
            history_store=store,
            clock=lambda: fixed_now,
            provider_revisions={"superpowers": {"old": "a", "new": "b"}},
        ).apply(
            plan,
            approved=True,
        )

    assert (tmp_path / "AGENTS.md").read_bytes() == b"old AGENTS.md\n"
    assert HistoryStore(path).list() == [_entry()]


def test_failed_append_compensation_preserves_an_independent_successful_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".ai" / "history.jsonl"
    independent_entry = _entry().model_copy(
        update={"timestamp": "2026-08-11T16:00:00Z", "files_changed": ["CODEX.md"]}
    )

    class AppendOtherThenCommitAndRaiseStore(HistoryStore):
        _fail_after_commit = False

        def append(self, entry: HistoryEntry) -> None:
            HistoryStore(self.path).append(independent_entry)
            self._fail_after_commit = True
            super().append(entry)

        def _atomic_replace(self, payload: bytes) -> None:
            super()._atomic_replace(payload)
            if self._fail_after_commit:
                self._fail_after_commit = False
                raise OSError("injected failure after history commit")

    plan = _plan(tmp_path, "AGENTS.md")
    store = AppendOtherThenCommitAndRaiseStore(path)

    with pytest.raises(RuntimeError, match="audit"):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert (tmp_path / "AGENTS.md").read_bytes() == b"old AGENTS.md\n"
    assert HistoryStore(path).list() == [independent_entry]


def test_failed_audit_compensation_reports_that_configuration_remains_applied(
    tmp_path: Path,
) -> None:
    class UncompensatableCommittedStore(HistoryStore):
        def _atomic_replace(self, payload: bytes) -> None:
            super()._atomic_replace(payload)
            raise OSError("injected failure after history commit")

        def compensate_append(
            self,
            token: history_module.HistoryAppendToken,
            entry: HistoryEntry,
        ) -> None:
            raise OSError("injected compensation failure")

    plan = _plan(tmp_path, "AGENTS.md")
    store = UncompensatableCommittedStore(tmp_path / ".ai" / "history.jsonl")

    with pytest.raises(
        SyncAuditCompensationError,
        match="configuration remains applied",
    ):
        SyncApplier(history_store=store).apply(plan, approved=True)

    assert (tmp_path / "AGENTS.md").read_bytes() == b"new AGENTS.md\n"
    assert len(store.list()) == 1


def test_managed_block_sync_does_not_claim_inverse_without_durable_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "AGENTS.md"
    before = (
        b"# user prefix\n"
        b"<!-- skillcord:begin -->\nold policy\n<!-- skillcord:end -->\n"
        b"user suffix\n"
    )
    after = (
        b"# user prefix\n"
        b"<!-- skillcord:begin -->\nnew policy\n<!-- skillcord:end -->\n"
        b"user suffix\n"
    )
    target.write_bytes(before)
    plan = SyncPlan(
        project_root=tmp_path,
        changes=(
            PlannedFileChange(
                path=target,
                relative_path=Path("AGENTS.md"),
                ownership="managed_block",
                before=before,
                after=after,
            ),
        ),
    )
    store = HistoryStore(tmp_path / ".ai" / "history.jsonl")
    fixed_now = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)

    result = SyncApplier(
        history_store=store,
        clock=lambda: fixed_now,
        provider_revisions={"superpowers": {"old": "a", "new": "b"}},
    ).apply(plan, approved=True)

    assert result.applied is True
    assert target.read_bytes() == after
    assert len(store.list()) == 1
    assert store.list()[0] == HistoryEntry(
        timestamp="2026-08-11T17:00:00Z",
        action="sync",
        files_changed=["AGENTS.md"],
        provider_revisions={"superpowers": {"old": "a", "new": "b"}},
        deterministic_inverse=False,
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
