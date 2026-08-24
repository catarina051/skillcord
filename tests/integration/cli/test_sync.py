from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_sync_requires_approval_before_writing(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("user content\n", encoding="utf-8")
    initialized = CliRunner().invoke(
        app,
        ["init", "--project-root", str(tmp_path), "--harness", "claude"],
    )
    assert initialized.exit_code == 0

    result = CliRunner().invoke(app, ["sync", "--project-root", str(tmp_path)], input="n\n")

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "user content\n"


def test_sync_approval_changes_only_the_managed_block(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("user content\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(
        app,
        ["init", "--project-root", str(tmp_path), "--harness", "claude"],
    ).exit_code == 0

    result = runner.invoke(app, ["sync", "--project-root", str(tmp_path)], input="y\n")

    assert result.exit_code == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("user content\n")
    assert text.count("skillcord:begin") == 1
