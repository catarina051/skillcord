from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_doctor_reports_not_ready_when_project_is_not_initialized(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["doctor", "--project-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "schema.project" in result.stdout
