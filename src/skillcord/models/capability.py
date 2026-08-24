"""Deterministic capability-overlap schemas."""

from pydantic import BaseModel, Field

from skillcord.models.provider import SkillRecord


class CapabilityGroup(BaseModel):
    """Skills that have deterministic evidence of the same capability."""

    capability_id: str
    candidates: list[SkillRecord] = Field(default_factory=list)
    default_action: str = "keep-all"
    action: str = "keep-all"
    single_owner_required: bool = False
