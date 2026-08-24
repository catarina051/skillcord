"""Immutable-lock schemas for resolved provider content."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LockArtifact(BaseModel):
    """A resolved artifact and its raw-content hash."""

    path: Path
    sha256: str


class LockComponent(BaseModel):
    """A source component and its resolved artifacts."""

    component_id: str
    source_path: Path
    source_revision: str | None = None
    updater_can_mutate: bool = False
    artifacts: dict[str, LockArtifact] = Field(default_factory=dict)


class LockProvider(BaseModel):
    """The locked components belonging to one provider."""

    provider_id: str
    components: list[LockComponent] = Field(default_factory=list)


class LockFile(BaseModel):
    """The schema for ``.ai/ai-lock.yaml``."""

    schema_version: Literal[1]
    skillcord_schema_version: Literal[1]
    providers: dict[str, LockProvider] = Field(default_factory=dict)
    resolved_ids: set[str] = Field(default_factory=set)
