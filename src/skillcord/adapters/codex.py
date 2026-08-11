"""Codex adapter generation."""

from skillcord.adapters.agents_md import AgentsMdAdapter
from skillcord.adapters.base import AdapterContext, GeneratedArtifact


class CodexAdapter:
    """Use Codex's native ``AGENTS.md`` support without duplicating policy."""

    harness_id = "codex"

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        """Return only the shared universal policy artifact."""

        return AgentsMdAdapter().plan(context)
