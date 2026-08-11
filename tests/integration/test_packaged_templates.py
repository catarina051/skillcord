"""Exercise policy rendering from an isolated wheel-style installation layout."""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


def test_wheel_layout_contains_templates_and_renders_policy(tmp_path: Path) -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    wheel_config = config["tool"]["hatch"]["build"]["targets"]["wheel"]
    force_include: dict[str, str] = wheel_config.get("force-include", {})
    if not force_include:
        pytest.fail("wheel configuration does not include template resources")

    site_packages = tmp_path / "site-packages"
    shutil.copytree(Path("src/skillcord"), site_packages / "skillcord")
    for source, destination in force_include.items():
        shutil.copytree(Path(source), site_packages / destination, dirs_exist_ok=True)

    script = """
from pathlib import Path
import sys

import skillcord
from skillcord.adapters.agents_md import render_agents_policy

site_packages = Path(sys.argv[1]).resolve()
package_file = Path(skillcord.__file__).resolve()
assert package_file.is_relative_to(site_packages), package_file
text = render_agents_policy({"humanizer.humanizer", "open_design.ui-review_v2"})
assert "explicitly requests prose humanization" in text
assert "Do not automatically apply" in text
assert "open_design.ui-review_v2" in text
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(site_packages)
    result = subprocess.run(
        [sys.executable, "-c", script, str(site_packages)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
