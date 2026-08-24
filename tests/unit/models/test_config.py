import pytest
from pydantic import ValidationError

from skillcord.models.config import ProjectConfig


def test_project_config_accepts_ai_only_fields() -> None:
    cfg = ProjectConfig.model_validate(
        {
            "schema_version": 1,
            "project": {"intent": "fullstack_web_application"},
            "ai": {
                "harnesses": ["claude", "codex"],
                "desired_capabilities": ["planning", "security"],
            },
        }
    )
    assert cfg.ai.harnesses == ["claude", "codex"]


def test_project_config_rejects_runtime_requirements() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(
            {
                "schema_version": 1,
                "project": {"intent": "api"},
                "ai": {"harnesses": ["claude"], "desired_capabilities": []},
                "requirements": {"node": ">=20"},
            }
        )
