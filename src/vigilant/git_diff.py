import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

FILE_STATUSES = {"A": "added", "M": "modified", "D": "deleted"}


class GitError(Exception):
    pass


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    old_source: bytes | None
    new_source: bytes | None


def changed_python_files(repo: Path, base: str, target: str) -> list[FileChange]:
    """Return the Python files that differ between two revisions, sorted by path."""
    _git(repo, "rev-parse", "--git-dir")
    base_commit = _resolve_commit(repo, base)
    target_commit = _resolve_commit(repo, target)

    output = _git(
        repo,
        "diff-tree",
        "-r",
        "-z",
        "--no-renames",
        "--name-status",
        "--diff-filter=AMD",
        base_commit,
        target_commit,
        "--",
        ":(top)*.py",
    )
    fields = output.split(b"\0")[:-1]

    changes = []
    for status, raw_path in zip(fields[::2], fields[1::2]):
        path = os.fsdecode(raw_path)
        status_name = FILE_STATUSES[status.decode()]
        old_source = None if status_name == "added" else _blob(repo, base_commit, path)
        new_source = (
            None if status_name == "deleted" else _blob(repo, target_commit, path)
        )
        changes.append(FileChange(path, status_name, old_source, new_source))
    return sorted(changes, key=lambda change: change.path)


def _resolve_commit(repo: Path, revision: str) -> str:
    try:
        output = _git(
            repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
        )
    except GitError as error:
        raise GitError(f"unknown revision: {revision}") from error
    return output.decode().strip()


def _blob(repo: Path, commit: str, path: str) -> bytes:
    return _git(repo, "cat-file", "blob", f"{commit}:{path}")


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    except FileNotFoundError as error:
        raise GitError("git executable not found") from error
    if result.returncode != 0:
        raise GitError(result.stderr.decode(errors="replace").strip())
    return result.stdout
