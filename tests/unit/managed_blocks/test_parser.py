"""Tests for strict managed-block marker parsing."""

import pytest

from skillcord.managed_blocks.parser import ManagedBlockError, parse_managed_block


def test_parse_managed_block_reports_the_single_block_boundaries() -> None:
    text = "prefix\n<!-- skillcord:begin -->\nmanaged\n<!-- skillcord:end -->\nsuffix\n"

    state = parse_managed_block(text)

    assert state.present is True
    assert text[state.start : state.end] == "<!-- skillcord:begin -->\nmanaged\n<!-- skillcord:end -->\n"


@pytest.mark.parametrize(
    "text",
    [
        "<!-- skillcord:begin -->\nmanaged\n",
        "managed\n<!-- skillcord:end -->\n",
        (
            "<!-- skillcord:begin -->\none\n<!-- skillcord:end -->\n"
            "<!-- skillcord:begin -->\ntwo\n<!-- skillcord:end -->\n"
        ),
        "<!-- skillcord:begin -->\none\n<!-- skillcord:begin -->\ntwo\n<!-- skillcord:end -->\n",
    ],
    ids=["begin_without_end", "end_without_begin", "duplicate_blocks", "nested_begin"],
)
def test_parse_managed_block_rejects_malformed_markers(text: str) -> None:
    with pytest.raises(ManagedBlockError):
        parse_managed_block(text)


def test_parse_managed_block_reports_when_no_block_exists() -> None:
    state = parse_managed_block("# User instructions\n")

    assert state.present is False
    assert state.start is None
    assert state.end is None


def test_parse_managed_block_accepts_marker_lines_with_spaces_and_tabs() -> None:
    text = "prefix\n \t<!-- skillcord:begin -->\t \nmanaged\n\t<!-- skillcord:end --> \nsuffix\n"

    state = parse_managed_block(text)

    assert state.present is True
    assert text[state.start : state.end].startswith(" \t<!-- skillcord:begin -->")
