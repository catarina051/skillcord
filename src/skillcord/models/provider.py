"""Normalized provider-discovery schemas."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from skillcord.normalization.ids import validate_canonical_identifier


class SkillRecord(BaseModel):
    """A normalized skill discovered from one provider."""

    provider_id: str
    skill_id: str
    name: str
    description: str | None = None
    source_path: Path
    source_revision: str | None = None
    content_hash: str
    capabilities: set[str] = Field(default_factory=set)
    harnesses: set[str] = Field(default_factory=set)
    optional: bool = False
    execution_mode: Literal["provider", "harness_only"] = "provider"

    @field_validator("provider_id", "skill_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        """Reject identifiers that could escape generated policy references."""

        return validate_canonical_identifier(value)

    @property
    def normalized_id(self) -> str:
        """Return the provider-scoped stable skill identifier."""

        return f"{self.provider_id}.{self.skill_id}"


class ProviderComponent(BaseModel):
    """A provider source component participating in a discovery snapshot."""

    component_id: str
    source_path: Path
    source_revision: str | None = None
    updater_can_mutate: bool = False
    artifact_hashes: dict[Path, str] = Field(default_factory=dict)

    @property
    def revision(self) -> str | None:
        """Return the optional immutable source revision for compatibility."""

        return self.source_revision


class UnsupportedAsset(BaseModel):
    """A provider artifact discovered but deliberately not activated by V1."""

    kind: str
    source_path: Path
    reason: str


class ProviderSnapshot(BaseModel):
    """The complete discovered state for one provider."""

    provider_id: str
    root_path: Path
    skills: list[SkillRecord] = Field(default_factory=list)
    components: list[ProviderComponent] = Field(default_factory=list)
    runtime_requirements: dict[str, str] = Field(default_factory=dict)
    partial_support: bool = False
    unsupported_assets: list[UnsupportedAsset] = Field(default_factory=list)

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        """Keep provider snapshot identities within the canonical grammar."""

        return validate_canonical_identifier(value)
