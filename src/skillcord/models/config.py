"""Project-scoped AI configuration schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectInfo(BaseModel):
    """The project's AI-relevant intent."""

    model_config = ConfigDict(extra="forbid")

    intent: str


class AIConfig(BaseModel):
    """Requested harnesses and capabilities for a project."""

    model_config = ConfigDict(extra="forbid")

    harnesses: list[str] = Field(default_factory=list)
    desired_capabilities: list[str] = Field(default_factory=list)


class CapabilityOverride(BaseModel):
    """An explicit capability ownership decision."""

    model_config = ConfigDict(extra="forbid")

    prefer: str | None = None
    suppress: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    """The schema for ``.ai/project.yaml``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    project: ProjectInfo
    ai: AIConfig


class OverrideConfig(BaseModel):
    """The schema for ``.ai/overrides.yaml``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    overrides: dict[str, CapabilityOverride] = Field(default_factory=dict)
