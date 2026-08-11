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
    ("original", "newline"),
    [
        (b"first\rsecond\r", b"\r"),
        (b"first\r\nsecond\n", b"\r\n"),
    ],
    ids=["cr_only", "mixed_uses_first_newline"],
)
def test_update_uses_the_first_detected_newline_convention(original: bytes, newline: bytes) -> None:
    updated = update_managed_block(original, b"managed\n")

    assert updated == (
        original + b"<!-- skillcord:begin -->" + newline + b"managed" + newline
        + b"<!-- skillcord:end -->" + newline
    )


def test_update_preserves_eof_user_bytes_without_final_newline() -> None:
    prefix = b"prefix\n"
    suffix = b"user text at eof  "
    original = prefix + b"<!-- skillcord:begin -->\nold\n<!-- skillcord:end -->\n" + suffix

    updated = update_managed_block(original, b"replacement\n")

    assert updated.startswith(prefix)
    assert updated.endswith(suffix)


def test_update_accepts_existing_marker_lines_with_spaces_and_tabs() -> None:
    original = (
        b"prefix\n"
        b" \t<!-- skillcord:begin -->\t \n"
        b"old\n"
        b"\t<!-- skillcord:end --> \n"
        b"suffix\n"
    )

    updated = update_managed_block(original, b"replacement\n")

    assert updated == (
        b"prefix\n<!-- skillcord:begin -->\nreplacement\n<!-- skillcord:end -->\nsuffix\n"
    )


@pytest.mark.parametrize(
    "replacement",
    [
        b"safe\n<!-- skillcord:begin -->\n",
        b"safe\n \t<!-- skillcord:end -->\t \n",
    ],
    ids=["begin_marker", "end_marker_with_spaces"],
)
def test_update_rejects_managed_markers_in_replacement_without_mutating_input(
    replacement: bytes,
) -> None:
    original = b"prefix\n<!-- skillcord:begin -->\nold\n<!-- skillcord:end -->\nsuffix\n"
    before = original

    with pytest.raises(ManagedBlockError):
        update_managed_block(original, replacement)

    assert original == before


def test_update_is_idempotent_for_a_clean_replacement() -> None:
    original = b"prefix\r\n<!-- skillcord:begin -->\r\nold\r\n<!-- skillcord:end -->\r\nsuffix\r\n"

    once = update_managed_block(original, b"managed\ncontent\n")
    twice = update_managed_block(once, b"managed\ncontent\n")

    assert twice == once


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
