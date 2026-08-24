"""Strict parsing and rendering for Skillcord-owned file regions."""

from skillcord.managed_blocks.parser import (
    ManagedBlockError,
    ManagedBlockState,
    parse_managed_block,
)
from skillcord.managed_blocks.writer import render_managed_update, update_managed_block

__all__ = [
    "ManagedBlockError",
    "ManagedBlockState",
    "parse_managed_block",
    "render_managed_update",
    "update_managed_block",
]
