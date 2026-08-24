"""Claude Code adapter generation."""

from pathlib import Path

from skillcord.adapters.agents_md import AgentsMdAdapter, _template_environment
from skillcord.adapters.base import AdapterContext, GeneratedArtifact, active_capability_ids


class ClaudeAdapter:
    """Plan universal policy plus a minimal Claude Code reference."""

    harness_id = "claude"

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        """Return shared policy first, followed by Claude-specific configuration."""

        source_ids = active_capability_ids(context)
        universal = AgentsMdAdapter().plan(context)
        claude_content = _template_environment().get_template("claude_managed.md.j2").render()
        return [
            *universal,
            GeneratedArtifact(
                path=Path("CLAUDE.md"),
                ownership="managed_block",
                content=claude_content.encode("utf-8"),
                source_capability_ids=source_ids,
            ),
        ]
