import json
import tomllib
from pathlib import Path

import freshlint
from freshlint.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_version_flag_prints_package_version(capsys):
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == f"freshlint {freshlint.__version__}"


def test_version_is_0_2_0():
    assert freshlint.__version__ == "0.2.0"


def test_package_pyproject_and_plugin_versions_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert pyproject["project"]["version"] == freshlint.__version__
    assert plugin["version"] == freshlint.__version__
