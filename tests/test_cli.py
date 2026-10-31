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
    def run(*args, cwd=None):
        return subprocess.run(
            [*request.param, *args], capture_output=True, text=True, cwd=cwd
        )

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


def test_scan_reports_signature_change_with_location(vigilant, git_repo):
    base = git_repo.commit(
        {"app.py": 'def create_user(name, role="user"):\n    pass\n'}
    )
    target = git_repo.commit(
        {"app.py": 'def create_user(name, role="viewer"):\n    pass\n'}
    )

    result = vigilant("scan", base, target, cwd=git_repo.path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "app.py (modified)\n"
        "  create_user (line 1)\n"
        "    default_changed: role: 'user' -> 'viewer'\n"
        "    - def create_user(name, role='user')\n"
        "    + def create_user(name, role='viewer')\n"
        "1 function signature change(s) found.\n"
    )


def test_scan_target_defaults_to_head(vigilant, git_repo):
    base = git_repo.commit()
    git_repo.commit({"app.py": "def f(): pass\n"})

    result = vigilant("scan", base, cwd=git_repo.path)

    assert result.returncode == 0
    assert "function_added: f" in result.stdout
    assert "app.py (added)" in result.stdout


def test_scan_groups_several_changes_to_one_function(vigilant, git_repo):
    base = git_repo.commit({"app.py": "def f(a): pass\n"})
    target = git_repo.commit({"app.py": "def f(a, b, c): pass\n"})

    result = vigilant("scan", base, target, cwd=git_repo.path)

    assert result.stdout.count("  f (line 1)") == 1
    assert "parameter_added: b" in result.stdout
    assert "parameter_added: c" in result.stdout
    assert "2 function signature change(s) found." in result.stdout


def test_scan_reports_deleted_file(vigilant, git_repo):
    base = git_repo.commit({"app.py": "def f(): pass\n"})
    target = git_repo.commit(delete=["app.py"])

    result = vigilant("scan", base, target, cwd=git_repo.path)

    assert result.returncode == 0
    assert "app.py (deleted)" in result.stdout
    assert "function_removed: f" in result.stdout


def test_scan_without_signature_changes(vigilant, git_repo):
    base = git_repo.commit({"app.py": "def f():\n    return 1\n", "README.md": "old\n"})
    target = git_repo.commit(
        {"app.py": "def f():\n    return 2\n", "README.md": "new\n"}
    )

    result = vigilant("scan", base, target, cwd=git_repo.path)

    assert result.returncode == 0
    assert result.stdout == "No function signature changes found.\n"


def test_scan_skips_invalid_python_and_reports_other_files(vigilant, git_repo):
    base = git_repo.commit({"bad.py": "def f(): pass\n", "good.py": "def g(): pass\n"})
    target = git_repo.commit({"bad.py": "def f(:\n", "good.py": "def g(a): pass\n"})

    result = vigilant("scan", base, target, cwd=git_repo.path)

    assert result.returncode == 0
    assert "warning: skipped bad.py: invalid Python in target revision" in result.stderr
    assert "parameter_added: a" in result.stdout
    assert "bad.py" not in result.stdout


def test_scan_invalid_revision_fails_with_clear_error(vigilant, git_repo):
    commit = git_repo.commit()

    result = vigilant("scan", commit, "no-such-branch", cwd=git_repo.path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "vigilant: error: unknown revision: no-such-branch\n"


def test_scan_outside_a_repository_fails(vigilant, tmp_path):
    result = vigilant("scan", "HEAD~1", cwd=tmp_path)

    assert result.returncode == 1
    assert "not a git repository" in result.stderr


def test_scan_requires_a_base_revision(vigilant):
    result = vigilant("scan")

    assert result.returncode == 2
    assert "base" in result.stderr
