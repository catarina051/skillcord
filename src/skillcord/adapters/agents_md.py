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


def _source_checkout_template_root() -> Path | None:
    """Return templates only when this module is in this repository's source layout."""

    module_path = Path(__file__).resolve()
    checkout_root = module_path.parents[3]
    expected_module = checkout_root / "src" / "skillcord" / "adapters" / "agents_md.py"
    repository_sentinels = (
        checkout_root / ".git",
        checkout_root / "AGENTS.md",
        checkout_root / "pyproject.toml",
        checkout_root
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-08-11-skillcord-runtime-design.md",
    )
    try:
        module_matches = expected_module.samefile(module_path)
    except (FileNotFoundError, OSError):
        return None
    if not module_matches or not all(sentinel.exists() for sentinel in repository_sentinels):
        return None

    template_root = checkout_root / "templates"
    return template_root if template_root.is_dir() else None


_SOURCE_TEMPLATE_ROOT = _source_checkout_template_root()


def _load_template(name: str) -> str | None:
    """Load a packaged template, with an explicit source-tree fallback for editable use."""

    if Path(name).name != name:
        return None

    packaged_template = files("skillcord").joinpath("templates", name)
    if packaged_template.is_file():
        return packaged_template.read_text(encoding="utf-8")

    if _SOURCE_TEMPLATE_ROOT is not None:
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
