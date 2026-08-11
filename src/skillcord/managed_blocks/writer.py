"""Byte-oriented updates for the Skillcord-managed file block."""

from dataclasses import dataclass

from skillcord.managed_blocks.parser import ManagedBlockError

_BEGIN_MARKER = b"<!-- skillcord:begin -->"
_END_MARKER = b"<!-- skillcord:end -->"


@dataclass(frozen=True)
class _ByteBlock:
    start: int
    end: int


def _marker_for_line(line: bytes) -> bytes | None:
    candidate = line.rstrip(b"\r\n").strip(b" \t")
    if candidate == _BEGIN_MARKER or candidate == _END_MARKER:
        return candidate
    return None


def _find_byte_block(original: bytes) -> _ByteBlock | None:
    """Find a marker block without decoding arbitrary user-authored bytes."""
    block_start: int | None = None
    block_end: int | None = None
    offset = 0

    for line in original.splitlines(keepends=True):
        marker = _marker_for_line(line)
        if marker == _BEGIN_MARKER:
            if block_start is not None and block_end is None:
                raise ManagedBlockError("nested Skillcord managed-block begin marker")
            if block_end is not None:
                raise ManagedBlockError("duplicate Skillcord managed block")
            block_start = offset
        elif marker == _END_MARKER:
            if block_start is None:
                raise ManagedBlockError("Skillcord managed-block end marker without begin marker")
            if block_end is not None:
                raise ManagedBlockError("duplicate Skillcord managed block")
            block_end = offset + len(line)
        offset += len(line)

    if block_start is None:
        return None
    if block_end is None:
        raise ManagedBlockError("Skillcord managed-block begin marker without end marker")
    return _ByteBlock(start=block_start, end=block_end)


def _detected_newline(content: bytes) -> bytes:
    """Return the first line-ending convention used by the existing content."""
    index = 0
    while index < len(content):
        current = content[index : index + 1]
        if current == b"\r":
            if content[index + 1 : index + 2] == b"\n":
                return b"\r\n"
            return b"\r"
        if current == b"\n":
            return b"\n"
        index += 1
    return b"\n"


def _normalise_newlines(content: bytes, newline: bytes) -> bytes:
    """Use the target file's line convention for generated managed content."""
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", newline)


def _render_block(replacement: bytes, newline: bytes) -> bytes:
    body = _normalise_newlines(replacement, newline)
    if body and not body.endswith(newline):
        body += newline
    return _BEGIN_MARKER + newline + body + _END_MARKER + newline


def render_managed_update(original: bytes, replacement: bytes) -> bytes:
    """Return a safe managed-block update while preserving every outside byte.

    Existing malformed marker states raise :class:`ManagedBlockError`; no
    repair or partial replacement is attempted.  A missing block is appended
    after the original bytes, using the existing line convention (or LF for an
    empty file).
    """
    block = _find_byte_block(original)
    newline = _detected_newline(original)
    rendered = _render_block(replacement, newline)

    if block is not None:
        return original[: block.start] + rendered + original[block.end :]
    if not original:
        return rendered
    separator = b"" if original.endswith((b"\r", b"\n")) else newline
    return original + separator + rendered


def update_managed_block(original: bytes, replacement: bytes) -> bytes:
    """Compatibility name for rendering a strict managed-block update."""
    return render_managed_update(original, replacement)
