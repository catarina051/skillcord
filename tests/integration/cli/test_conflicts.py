from typer.testing import CliRunner

from skillcord.cli.app import app


def test_conflicts_reports_the_default_question_budget() -> None:
    result = CliRunner().invoke(app, ["conflicts"])

    assert result.exit_code == 0
    assert "Question budget: 10" in result.stdout
    assert "Keep-all beyond budget: 0" in result.stdout
