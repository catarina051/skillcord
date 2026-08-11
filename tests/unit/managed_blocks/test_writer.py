"""Tests for raw-byte managed-block updates."""

import pytest

from skillcord.managed_blocks.parser import ManagedBlockError
from skillcord.managed_blocks.writer import render_managed_update, update_managed_block


def test_user_content_outside_block_is_byte_preserved() -> None:
    original = (
        b"# My Project\r\n"
        b"User text  \r\n"
        b"<!-- skillcord:begin -->\r\nold\r\n<!-- skillcord:end -->\r\n"
        b"tail\r\n"
    )

    updated = update_managed_block(original, b"new\n")

    assert updated.startswith(b"# My Project\r\nUser text  \r\n")
    assert updated.endswith(b"tail\r\n")


def test_update_preserves_non_utf8_user_bytes_and_bom_outside_block() -> None:
    prefix = b"\xef\xbb\xbf# title\r\n\xff\x80 user text  \r\n"
    suffix = b"\x81 tail\r\n"
    original = prefix + b"<!-- skillcord:begin -->\r\nold\r\n<!-- skillcord:end -->\r\n" + suffix

    updated = update_managed_block(original, b"new\n")

    assert updated.startswith(prefix)
    assert updated.endswith(suffix)


def test_update_appends_missing_block_with_detected_newline_convention() -> None:
    original = b"# User content\r\nTrailing spaces  \r\n"

    updated = render_managed_update(original, b"managed\ncontent\n")

    assert updated == (
        original
        + b"<!-- skillcord:begin -->\r\nmanaged\r\ncontent\r\n<!-- skillcord:end -->\r\n"
    )


def test_update_appends_missing_block_to_empty_file_using_lf() -> None:
    assert update_managed_block(b"", b"managed\n") == (
        b"<!-- skillcord:begin -->\nmanaged\n<!-- skillcord:end -->\n"
    )


@pytest.mark.parametrize(
    "original",
    [
        b"<!-- skillcord:begin -->\nmanaged\n",
        b"managed\n<!-- skillcord:end -->\n",
        (
            b"<!-- skillcord:begin -->\none\n<!-- skillcord:end -->\n"
            b"<!-- skillcord:begin -->\ntwo\n<!-- skillcord:end -->\n"
        ),
        b"<!-- skillcord:begin -->\none\n<!-- skillcord:begin -->\ntwo\n<!-- skillcord:end -->\n",
    ],
    ids=["begin_without_end", "end_without_begin", "duplicate_blocks", "nested_begin"],
)
def test_update_rejects_malformed_markers_without_repair(original: bytes) -> None:
    with pytest.raises(ManagedBlockError):
        update_managed_block(original, b"replacement\n")
