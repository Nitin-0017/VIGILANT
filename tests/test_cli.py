import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONSOLE_SCRIPT = Path(sys.executable).parent / "vigilant"

INVOCATIONS = {
    "console-script": [str(CONSOLE_SCRIPT)],
    "module": [sys.executable, "-m", "vigilant"],
}


@pytest.fixture(params=INVOCATIONS.values(), ids=INVOCATIONS.keys())
def vigilant(request):
    def run(*args):
        return subprocess.run([*request.param, *args], capture_output=True, text=True)

    return run


def test_version_matches_pyproject(vigilant):
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    result = vigilant("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == f"vigilant {pyproject['project']['version']}"


def test_help_describes_usage(vigilant):
    result = vigilant("--help")

    assert result.returncode == 0
    assert result.stdout.startswith("usage: vigilant ")
    assert "Detect documentation drift" in result.stdout
    assert "--version" in result.stdout


def test_no_arguments_prints_help(vigilant):
    result = vigilant()

    assert result.returncode == 0
    assert result.stdout == vigilant("--help").stdout


def test_unknown_option_is_rejected(vigilant):
    result = vigilant("--nonexistent")

    assert result.returncode == 2
    assert "unrecognized arguments: --nonexistent" in result.stderr
    assert result.stdout == ""
