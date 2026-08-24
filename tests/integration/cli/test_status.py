import json
from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_status_json_writes_only_json_to_stdout(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["status", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ready"] is False
