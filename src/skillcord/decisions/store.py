"""YAML persistence for project-scoped capability decisions."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml  # type: ignore[import-untyped]

from skillcord.models.config import CapabilityOverride, OverrideConfig


class DecisionStore:
    """Load and atomically save a project's explicit capability overrides."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> OverrideConfig:
        """Load the configured overrides, or return an empty V1 configuration."""

        if not self.path.exists():
            return OverrideConfig(schema_version=1)

        with self.path.open(encoding="utf-8") as overrides_file:
            data = yaml.safe_load(overrides_file)
        return OverrideConfig.model_validate(data)

    def save(self, config: OverrideConfig) -> None:
        """Atomically write ``config`` in deterministic YAML order."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize_config(config)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                yaml.safe_dump(
                    payload,
                    temporary_file,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
                temporary_file.flush()
            temporary_path.replace(self.path)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def save_decision(
        self,
        capability_id: str,
        prefer: str | None,
        suppress: list[str],
    ) -> None:
        """Persist one explicit ownership decision without discarding existing decisions."""

        config = self.load()
        overrides = dict(config.overrides)
        overrides[capability_id] = CapabilityOverride(prefer=prefer, suppress=suppress)
        self.save(config.model_copy(update={"overrides": overrides}))


def _serialize_config(config: OverrideConfig) -> dict[str, Any]:
    """Return a stable YAML-ready representation of a validated override config."""

    return {
        "schema_version": config.schema_version,
        "overrides": {
            capability_id: {
                "prefer": override.prefer,
                "suppress": sorted(override.suppress),
            }
            for capability_id, override in sorted(config.overrides.items())
        },
    }
