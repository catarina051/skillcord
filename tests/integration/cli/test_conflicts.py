from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_conflicts_reports_the_default_question_budget() -> None:
    result = CliRunner().invoke(app, ["conflicts"])

    assert result.exit_code == 0
    assert "Question budget: 10" in result.stdout
    assert "Keep-all beyond budget: 0" in result.stdout


def test_conflicts_persists_interactive_preference_for_one_capability_group(tmp_path: Path) -> None:
    runner = CliRunner()
    for provider in ("one", "two"):
        root = tmp_path / provider
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\nname: Testing\ncapabilities: [testing]\n---\n",
            encoding="utf-8",
        )
    assert runner.invoke(
        app, ["init", "--project-root", str(tmp_path), "--harness", "claude"]
    ).exit_code == 0

    result = runner.invoke(
        app,
        [
            "conflicts",
            "--project-root",
            str(tmp_path),
            "--source",
            f"one={tmp_path / 'one'}",
            "--source",
            f"two={tmp_path / 'two'}",
        ],
        input="1\n",
    )

    assert result.exit_code == 0
    text = (tmp_path / ".ai" / "overrides.yaml").read_text(encoding="utf-8")
    assert "prefer: one.testing" in text
