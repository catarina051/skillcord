"""Deterministic, fail-closed JSONL history for Skillcord-managed changes."""

from __future__ import annotations

import builtins
import importlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import Lock, RLock
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_PATH_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[str, RLock] = {}


def _shared_path_lock(path: Path) -> RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _load_symbol(module_name: str, symbol_name: str) -> object:
    return vars(importlib.import_module(module_name))[symbol_name]


class _InterprocessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def __enter__(self) -> None:
        if self.path.is_symlink():
            raise HistoryFormatError(
                f"history lock path must not be a symbolic link: {self.path}"
            )
        descriptor = os.open(
            self.path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        self._descriptor = descriptor
        try:
            opened = os.fstat(descriptor)
            named = self.path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(named.st_mode)
                or not os.path.samestat(opened, named)
            ):
                raise HistoryFormatError(
                    f"history lock path must be a regular file: {self.path}"
                )
            if os.name == "nt":
                locking = cast(
                    Callable[[int, int, int], object],
                    _load_symbol("msvcrt", "locking"),
                )
                lock_mode = cast(int, _load_symbol("msvcrt", "LK_LOCK"))
                os.lseek(descriptor, 0, os.SEEK_SET)
                locking(descriptor, lock_mode, 1)
            else:
                flock = cast(
                    Callable[[int, int], object],
                    _load_symbol("fcntl", "flock"),
                )
                lock_mode = cast(int, _load_symbol("fcntl", "LOCK_EX"))
                flock(descriptor, lock_mode)
        except BaseException:
            os.close(descriptor)
            self._descriptor = None
            raise

    def __exit__(self, *_exc_info: object) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        try:
            if os.name == "nt":
                locking = cast(
                    Callable[[int, int, int], object],
                    _load_symbol("msvcrt", "locking"),
                )
                unlock_mode = cast(int, _load_symbol("msvcrt", "LK_UNLCK"))
                os.lseek(descriptor, 0, os.SEEK_SET)
                locking(descriptor, unlock_mode, 1)
            else:
                flock = cast(
                    Callable[[int, int], object],
                    _load_symbol("fcntl", "flock"),
                )
                unlock_mode = cast(int, _load_symbol("fcntl", "LOCK_UN"))
                flock(descriptor, unlock_mode)
        finally:
            os.close(descriptor)
            self._descriptor = None


class HistoryFormatError(ValueError):
    """Raised when existing audit history cannot be trusted or decoded."""


class _AmbiguousJSONError(ValueError):
    """Raised when JSON syntax is valid but not unambiguous."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _AmbiguousJSONError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise _AmbiguousJSONError(f"non-standard JSON constant: {value}")


@dataclass(frozen=True)
class HistoryCheckpoint:
    """Exact history bytes captured before a guarded sync begins."""

    existed: bool
    content: bytes
    entries: tuple[HistoryEntry, ...]


class ProviderRevision(BaseModel):
    """The prior and newly applied immutable revision, when available."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    old: str | None = None
    new: str | None = None

    @field_validator("old", "new")
    @classmethod
    def _validate_revision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or value != value.strip()
            or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
        ):
            raise ValueError("provider revisions must be non-blank canonical strings")
        return value


class HistoryEntry(BaseModel):
    """One successfully committed Skillcord configuration change."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    timestamp: str
    action: str = Field(min_length=1)
    files_changed: list[str] = Field(min_length=1)
    provider_revisions: dict[str, ProviderRevision] = Field(default_factory=dict)
    deterministic_inverse: bool

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: str) -> str:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
            raise ValueError("history timestamp must be canonical ISO-8601 UTC")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("history timestamp must be an ISO-8601 UTC value") from error
        if parsed.utcoffset() != timedelta(0):
            raise ValueError("history timestamp must be UTC")
        return value

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        if (
            not value
            or value != value.strip()
            or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
        ):
            raise ValueError("history action must be a canonical non-blank string")
        return value

    @field_validator("files_changed")
    @classmethod
    def _validate_files_changed(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("history file paths must not contain duplicates")
        for value in values:
            path = PurePosixPath(value)
            if (
                not value
                or "\\" in value
                or re.match(r"^[A-Za-z]:", value) is not None
                or path.is_absolute()
                or path == PurePosixPath(".")
                or ".." in path.parts
                or path.as_posix() != value
                or any(
                    character.isspace()
                    or unicodedata.category(character) in {"Cc", "Cf"}
                    for character in value
                )
            ):
                raise ValueError("history file paths must be canonical safe relative POSIX paths")
        return values

    @field_validator("provider_revisions")
    @classmethod
    def _validate_provider_ids(
        cls,
        values: dict[str, ProviderRevision],
    ) -> dict[str, ProviderRevision]:
        if any(
            re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", provider_id) is None
            for provider_id in values
        ):
            raise ValueError("history provider IDs must be normalized tokens")
        return values


class HistoryStore:
    """Read and atomically append validated entries to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = _shared_path_lock(path)
        self._lock_path = path.with_name(f".{path.name}.lock")

    def append(self, entry: HistoryEntry) -> None:
        """Append one entry without exposing a partially written JSONL record."""

        validated = HistoryEntry.model_validate(entry.model_dump(mode="python"))
        self._read_entries()
        with self._coordinated():
            entries = self._read_entries()
            payload = self._serialize((*entries, validated))
            self._atomic_replace(payload)

    def list(self) -> builtins.list[HistoryEntry]:
        """Return entries in commit order, rejecting any malformed state."""

        if not self.path.exists() and not self.path.parent.exists():
            return []
        with self._coordinated():
            return self._read_entries()

    def checkpoint(self) -> HistoryCheckpoint:
        """Capture validated bytes so a failed sync audit can be compensated."""

        with self._coordinated():
            entries = tuple(self._read_entries())
            if not self.path.exists():
                return HistoryCheckpoint(existed=False, content=b"", entries=entries)
            return HistoryCheckpoint(
                existed=True,
                content=self.path.read_bytes(),
                entries=entries,
            )

    def compensate_append(
        self,
        checkpoint: HistoryCheckpoint,
        entry: HistoryEntry,
    ) -> None:
        """Remove only a failed append while preserving later successful appends."""

        validated = HistoryEntry.model_validate(entry.model_dump(mode="python"))
        with self._coordinated():
            current = self._read_entries()
            prefix = list(checkpoint.entries)
            if current[: len(prefix)] != prefix:
                raise HistoryFormatError(
                    "history changed non-append-only during audit compensation"
                )
            appended = current[len(prefix) :]
            try:
                relative_index = appended.index(validated)
            except ValueError:
                return
            absolute_index = len(prefix) + relative_index
            remaining = current[:absolute_index] + current[absolute_index + 1 :]
            if remaining == prefix:
                if checkpoint.existed:
                    self._atomic_replace(checkpoint.content)
                else:
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
            elif remaining:
                self._atomic_replace(self._serialize(tuple(remaining)))
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass

    @contextmanager
    def _coordinated(self) -> Iterator[None]:
        with self._thread_lock:
            parent = self.path.parent
            parent.mkdir(parents=True, exist_ok=True)
            if parent.is_symlink() or not parent.is_dir():
                raise HistoryFormatError(
                    f"history parent must be a real directory: {parent}"
                )
            with _InterprocessLock(self._lock_path):
                yield

    def _read_entries(self) -> builtins.list[HistoryEntry]:
        if self.path.is_symlink():
            raise HistoryFormatError(f"history path must not be a symbolic link: {self.path}")
        if not self.path.exists():
            return []
        if not self.path.is_file():
            raise HistoryFormatError(f"history path must be a regular file: {self.path}")

        raw = self.path.read_bytes()
        if raw == b"":
            return []

        entries: builtins.list[HistoryEntry] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line:
                raise HistoryFormatError(f"history line {line_number} is blank")
            try:
                payload = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_strict_json_object,
                    parse_constant=_reject_json_constant,
                )
                entries.append(HistoryEntry.model_validate(payload))
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                _AmbiguousJSONError,
                ValidationError,
                TypeError,
            ) as error:
                raise HistoryFormatError(
                    f"history line {line_number} is malformed"
                ) from error
        return entries

    @staticmethod
    def _serialize(entries: tuple[HistoryEntry, ...]) -> bytes:
        lines = [
            json.dumps(
                entry.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for entry in entries
        ]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _atomic_replace(self, payload: bytes) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise HistoryFormatError(f"history parent must be a real directory: {parent}")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".skillcord-history.tmp",
            dir=parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
