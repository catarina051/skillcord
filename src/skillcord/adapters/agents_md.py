"""Universal ``AGENTS.md`` policy generation."""

from collections.abc import Collection
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, FunctionLoader, StrictUndefined, select_autoescape

from skillcord.adapters.base import (
    AdapterContext,
    GeneratedArtifact,
    active_capability_ids,
)
from skillcord.normalization.ids import validate_canonical_capability_id

_HUMANIZER_ID = "humanizer.humanizer"
_SOURCE_TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "templates"


def _load_template(name: str) -> str | None:
    """Load a packaged template, with an explicit source-tree fallback for editable use."""

    if Path(name).name != name:
        return None

    packaged_template = files("skillcord").joinpath("templates", name)
    if packaged_template.is_file():
        return packaged_template.read_text(encoding="utf-8")

    source_template = _SOURCE_TEMPLATE_ROOT / name
    if source_template.is_file():
        return source_template.read_text(encoding="utf-8")
    return None


@lru_cache(maxsize=1)
def _template_environment() -> Environment:
    """Build the strict template environment used by all policy adapters."""

    return Environment(
        loader=FunctionLoader(_load_template),
        autoescape=select_autoescape(default_for_string=False, default=False),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_agents_policy(active_ids: Collection[str]) -> str:
    """Render universal policy from identifiers, never provider prompt bodies."""

    capability_ids = tuple(
        sorted({validate_canonical_capability_id(active_id) for active_id in active_ids})
    )
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
