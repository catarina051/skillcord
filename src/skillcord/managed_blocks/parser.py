"""Strict parser for the single Skillcord-managed block in a text file."""

from dataclasses import dataclass

BEGIN_MARKER = "<!-- skillcord:begin -->"
END_MARKER = "<!-- skillcord:end -->"


class ManagedBlockError(ValueError):
    """Raised when managed markers cannot describe exactly one safe block."""


@dataclass(frozen=True)
class ManagedBlockState:
    """Character offsets for the sole managed block, if it is present."""

    start: int | None
    end: int | None

    @property
    def present(self) -> bool:
        """Whether a complete managed block was found."""
        return self.start is not None


def _marker_for_line(line: str) -> str | None:
    """Return the marker for a marker-only line, preserving all other text."""
    candidate = line.rstrip("\r\n").strip(" \t")
    if candidate == BEGIN_MARKER or candidate == END_MARKER:
        return candidate
    return None


def parse_managed_block(text: str) -> ManagedBlockState:
    """Locate the sole managed block or fail closed for malformed markers.

    Markers must appear on their own line (surrounding spaces or tabs are
    allowed).  Parsing continues after a closed block so duplicate marker
    sequences cannot be silently ignored.
    """
    block_start: int | None = None
    block_end: int | None = None
    offset = 0

    for line in text.splitlines(keepends=True):
        marker = _marker_for_line(line)
        if marker == BEGIN_MARKER:
            if block_start is not None and block_end is None:
                raise ManagedBlockError("nested Skillcord managed-block begin marker")
            if block_end is not None:
                raise ManagedBlockError("duplicate Skillcord managed block")
            block_start = offset
        elif marker == END_MARKER:
            if block_start is None:
                raise ManagedBlockError("Skillcord managed-block end marker without begin marker")
            if block_end is not None:
                raise ManagedBlockError("duplicate Skillcord managed block")
            block_end = offset + len(line)
        offset += len(line)

    if block_start is None:
        return ManagedBlockState(start=None, end=None)
    if block_end is None:
        raise ManagedBlockError("Skillcord managed-block begin marker without end marker")
    return ManagedBlockState(start=block_start, end=block_end)
