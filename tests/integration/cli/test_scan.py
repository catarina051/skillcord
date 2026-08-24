from pathlib import Path

from typer.testing import CliRunner

from skillcord.cli.app import app


def test_scan_renders_discovery_in_stable_provider_order(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["scan", "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "Providers:\nHarnesses:\n"
