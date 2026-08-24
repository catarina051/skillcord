from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_history_reports_no_entries_for_new_project(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["history", "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "No history entries.\n"
