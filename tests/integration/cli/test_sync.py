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


def test_noninteractive_sync_accepts_explicit_keep_all_overlap(tmp_path: Path) -> None:
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
            "sync",
            "--project-root",
            str(tmp_path),
            "--non-interactive",
            "--yes",
            "--source",
            f"one={tmp_path / 'one'}",
            "--source",
            f"two={tmp_path / 'two'}",
        ],
    )

    assert result.exit_code == 0
    assert "one.testing" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_noninteractive_sync_refuses_malformed_managed_markers(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(
        app, ["init", "--project-root", str(tmp_path), "--harness", "claude"]
    ).exit_code == 0
    (tmp_path / "AGENTS.md").write_text("<!-- skillcord:begin -->\nbroken\n", encoding="utf-8")

    result = runner.invoke(
        app, ["sync", "--project-root", str(tmp_path), "--non-interactive", "--yes"]
    )

    assert result.exit_code == 3
    assert "without end marker" in result.stderr


def test_noninteractive_sync_rejects_missing_override_reference(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(
        app, ["init", "--project-root", str(tmp_path), "--harness", "claude"]
    ).exit_code == 0
    (tmp_path / ".ai" / "overrides.yaml").write_text(
        "schema_version: 1\noverrides:\n  testing:\n    prefer: missing.skill\n    suppress: []\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["sync", "--project-root", str(tmp_path), "--non-interactive", "--yes"]
    )

    assert result.exit_code == 1
    assert "missing.skill" in result.stderr
