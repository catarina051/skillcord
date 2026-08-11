"""Exercise packaged policy templates and fail-closed source fallback behavior."""

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


def _isolated_environment(package_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(package_root)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def test_built_wheel_contains_templates_and_renders_policy(tmp_path: Path) -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies: list[str] = config["project"]["optional-dependencies"]["dev"]
    assert any(dependency.startswith("hatchling") for dependency in dev_dependencies)

    distribution_directory = tmp_path / "dist"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "-t",
            "wheel",
            "-d",
            str(distribution_directory),
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr

    wheels = list(distribution_directory.glob("*.whl"))
    assert len(wheels) == 1
    extracted_wheel = tmp_path / "extracted-wheel"
    with zipfile.ZipFile(wheels[0]) as wheel:
        wheel.extractall(extracted_wheel)

    script = """
from pathlib import Path
import sys

import skillcord
from skillcord.adapters.agents_md import render_agents_policy

wheel_root = Path(sys.argv[1]).resolve()
package_file = Path(skillcord.__file__).resolve()
assert package_file.is_relative_to(wheel_root), package_file
text = render_agents_policy({"humanizer.humanizer", "open_design.ui-review_v2"})
assert "explicitly requests prose humanization" in text
assert "Do not automatically apply" in text
assert "open_design.ui-review_v2" in text
"""
    render = subprocess.run(
        [sys.executable, "-c", script, str(extracted_wheel)],
        cwd=tmp_path,
        env=_isolated_environment(extracted_wheel),
        capture_output=True,
        text=True,
        check=False,
    )

    assert render.returncode == 0, render.stderr


def test_installed_layout_rejects_ambient_ancestor_templates(tmp_path: Path) -> None:
    fake_checkout = tmp_path / "ambient" / "project"
    fake_source = fake_checkout / "src"
    shutil.copytree(Path("src/skillcord"), fake_source / "skillcord")
    ambient_templates = fake_checkout / "templates"
    ambient_templates.mkdir(parents=True)
    (ambient_templates / "agents_managed.md.j2").write_text(
        "AMBIENT TEMPLATE MUST NOT LOAD\n",
        encoding="utf-8",
    )

    script = """
from pathlib import Path
import sys

from jinja2 import TemplateNotFound
import skillcord
from skillcord.adapters.agents_md import render_agents_policy

source_root = Path(sys.argv[1]).resolve()
package_file = Path(skillcord.__file__).resolve()
assert package_file.is_relative_to(source_root), package_file
try:
    render_agents_policy({"superpowers.brainstorming"})
except TemplateNotFound:
    pass
else:
    raise AssertionError("ambient ancestor template was loaded")
"""
    render = subprocess.run(
        [sys.executable, "-c", script, str(fake_source)],
        cwd=tmp_path,
        env=_isolated_environment(fake_source),
        capture_output=True,
        text=True,
        check=False,
    )

    assert render.returncode == 0, render.stderr
