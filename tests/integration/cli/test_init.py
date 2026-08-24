from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app

runner = CliRunner()


def test_init_creates_ai_only_project_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--project-root", str(tmp_path), "--harness", "claude"])

    assert result.exit_code == 0
    text = (tmp_path / ".ai" / "project.yaml").read_text(encoding="utf-8")
    assert "harnesses:" in text
    assert "node:" not in text
