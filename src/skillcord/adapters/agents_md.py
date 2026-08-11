"""Universal ``AGENTS.md`` policy generation."""

from collections.abc import Collection
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from skillcord.adapters.base import (
    AdapterContext,
    GeneratedArtifact,
    active_capability_ids,
)

_HUMANIZER_ID = "humanizer.humanizer"
_TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "templates"


@lru_cache(maxsize=1)
def _template_environment() -> Environment:
    """Build the strict template environment used by all policy adapters."""

    return Environment(
        loader=FileSystemLoader(_TEMPLATE_ROOT),
        autoescape=select_autoescape(default_for_string=False, default=False),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_agents_policy(active_ids: Collection[str]) -> str:
    """Render universal policy from identifiers, never provider prompt bodies."""

    capability_ids = tuple(sorted(set(active_ids)))
    template = _template_environment().get_template("agents_managed.md.j2")
    return template.render(
        active_ids=capability_ids,
        humanizer_active=_HUMANIZER_ID in capability_ids,
    )


class AgentsMdAdapter:
    """Plan the universal managed policy consumed by compatible harnesses."""

    harness_id = "agents_md"

    def plan(self, context: AdapterContext) -> list[GeneratedArtifact]:
        """Return the universal managed-block artifact."""

        source_ids = active_capability_ids(context)
        return [
            GeneratedArtifact(
                path=Path("AGENTS.md"),
                ownership="managed_block",
                content=render_agents_policy(source_ids).encode("utf-8"),
                source_capability_ids=source_ids,
            )
        ]
