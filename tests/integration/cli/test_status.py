import json
from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_status_json_writes_only_json_to_stdout(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["status", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ready"] is False


def test_status_maps_malformed_override_schema_to_usage_exit(tmp_path: Path) -> None:
    overrides = tmp_path / ".ai" / "overrides.yaml"
    overrides.parent.mkdir()
    overrides.write_text("schema_version: not-an-integer\noverrides: {}\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["status", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 2
    assert result.stdout == ""
