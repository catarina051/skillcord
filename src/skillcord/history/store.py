"""Deterministic, fail-closed JSONL history for Skillcord-managed changes."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class HistoryFormatError(ValueError):
    """Raised when existing audit history cannot be trusted or decoded."""


class ProviderRevision(BaseModel):
    """The prior and newly applied immutable revision, when available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    old: str | None = None
    new: str | None = None


class HistoryEntry(BaseModel):
    """One successfully committed Skillcord configuration change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: str
    action: str = Field(min_length=1)
    files_changed: list[str] = Field(min_length=1)
    provider_revisions: dict[str, ProviderRevision] = Field(default_factory=dict)
    deterministic_inverse: bool

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: str) -> str:
        if not value.endswith("Z"):
            raise ValueError("history timestamp must be UTC and end with 'Z'")
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
        if not value.strip():
            raise ValueError("history action must not be blank")
        return value

    @field_validator("files_changed")
    @classmethod
    def _validate_files_changed(cls, values: list[str]) -> list[str]:
        for value in values:
            path = PurePosixPath(value)
            if (
                not value
                or "\\" in value
                or path.is_absolute()
                or path == PurePosixPath(".")
                or ".." in path.parts
            ):
                raise ValueError("history file paths must be relative POSIX paths")
        return values

    @field_validator("provider_revisions")
    @classmethod
    def _validate_provider_ids(
        cls,
        values: dict[str, ProviderRevision],
    ) -> dict[str, ProviderRevision]:
        if any(not provider_id.strip() for provider_id in values):
            raise ValueError("history provider IDs must not be blank")
        return values


class HistoryStore:
    """Read and atomically append validated entries to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def append(self, entry: HistoryEntry) -> None:
        """Append one entry without exposing a partially written JSONL record."""

        validated = HistoryEntry.model_validate(entry.model_dump(mode="python"))
        with self._lock:
            entries = self._read_entries()
            payload = self._serialize((*entries, validated))
            self._atomic_replace(payload)

    def list(self) -> builtins.list[HistoryEntry]:
        """Return entries in commit order, rejecting any malformed state."""

        with self._lock:
            return self._read_entries()

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
                payload = json.loads(line.decode("utf-8"))
                entries.append(HistoryEntry.model_validate(payload))
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
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
