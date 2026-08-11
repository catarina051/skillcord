"""YAML persistence for project-scoped capability decisions."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, TextIO

import yaml  # type: ignore[import-untyped]

from skillcord.models.config import CapabilityOverride, OverrideConfig


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: Any,
    node: Any,
    deep: bool = False,
) -> dict[Any, Any]:
    """Construct a mapping without PyYAML's default last-value-wins behavior."""

    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key ({key!r})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class DecisionStore:
    """Load and atomically save a project's explicit capability overrides."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> OverrideConfig:
        """Load the configured overrides, or return an empty V1 configuration."""

        if not self.path.exists():
            return OverrideConfig(schema_version=1)

        with self.path.open(encoding="utf-8") as overrides_file:
            data = _load_yaml(overrides_file)
        return OverrideConfig.model_validate(data)

    def save(self, config: OverrideConfig) -> None:
        """Validate any existing file, then atomically write ``config``."""

        if self.path.exists():
            self.load()
        self._write(config)

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
        self._write(config.model_copy(update={"overrides": overrides}))

    def _write(self, config: OverrideConfig) -> None:
        """Atomically write a config whose existing on-disk state was validated."""

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


def _load_yaml(overrides_file: TextIO) -> object:
    """Load one YAML document while rejecting duplicate keys at every nesting level."""

    return yaml.load(overrides_file, Loader=_UniqueKeySafeLoader)


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
