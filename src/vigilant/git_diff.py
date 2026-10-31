import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FILE_STATUSES = {"A": "added", "M": "modified", "D": "deleted"}
MARKDOWN_SUFFIXES = (".md", ".markdown")
HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


class GitError(Exception):
    pass


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    old_source: bytes | None
    new_source: bytes | None


def changed_python_files(
    repo: Path, base: str, target: str, code_paths: tuple[str, ...] = ()
) -> list[FileChange]:
    """Return the Python files that differ between two revisions, sorted by path.

    Non-empty code_paths limits the result to those repository-relative paths.
    """
    base_commit = resolve_commit(repo, base)
    target_commit = resolve_commit(repo, target)
    pathspecs = [_code_pathspec(path) for path in code_paths] or [":(top)*.py"]

    changes = []
    for status, path in _changed_paths(repo, base_commit, target_commit, pathspecs):
        status_name = FILE_STATUSES[status]
        old_source = None if status_name == "added" else _blob(repo, base_commit, path)
        new_source = (
            None if status_name == "deleted" else _blob(repo, target_commit, path)
        )
        changes.append(FileChange(path, status_name, old_source, new_source))
    return sorted(changes, key=lambda change: change.path)


def excluded_python_paths(
    repo: Path, base: str, target: str, code_paths: tuple[str, ...]
) -> list[str]:
    """List changed Python files that the given code paths leave out of a scan."""
    if not code_paths:
        return []
    base_commit = resolve_commit(repo, base)
    target_commit = resolve_commit(repo, target)
    specs = [_code_pathspec(path) for path in code_paths]
    selected = {p for _, p in _changed_paths(repo, base_commit, target_commit, specs)}
    everything = _changed_paths(repo, base_commit, target_commit, [":(top)*.py"])
    return sorted(path for _, path in everything if path not in selected)


def diff_line_ranges(
    repo: Path, base_commit: str, target_commit: str, path: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return a file's changed line ranges in the base and target versions."""
    output = _git(
        repo,
        "diff-tree",
        "-r",
        "-p",
        "-U0",
        "--no-renames",
        base_commit,
        target_commit,
        "--",
        f":(top,literal){path}",
    )
    old_ranges = []
    new_ranges = []
    for hunk in HUNK_HEADER.finditer(output):
        old_start, old_count, new_start, new_count = hunk.groups()
        _add_range(old_ranges, old_start, old_count)
        _add_range(new_ranges, new_start, new_count)
    return old_ranges, new_ranges


def read_file(repo: Path, commit: str, path: str) -> bytes:
    return _blob(repo, commit, path)


def markdown_files(repo: Path, revision: str) -> list[tuple[str, bytes]]:
    """Return the path and content of every Markdown file at a revision."""
    commit = resolve_commit(repo, revision)
    output = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)

    files = []
    for entry in output.split(b"\0")[:-1]:
        metadata, raw_path = entry.split(b"\t", 1)
        path = os.fsdecode(raw_path)
        is_regular_file = metadata.startswith(b"100")
        if is_regular_file and path.lower().endswith(MARKDOWN_SUFFIXES):
            files.append((path, _blob(repo, commit, path)))
    return sorted(files)


def _changed_paths(
    repo: Path, base_commit: str, target_commit: str, pathspecs: list[str]
) -> list[tuple[str, str]]:
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
        *pathspecs,
    )
    fields = output.split(b"\0")[:-1]
    return [
        (status.decode(), os.fsdecode(raw_path))
        for status, raw_path in zip(fields[::2], fields[1::2])
    ]


def _code_pathspec(path: str) -> str:
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or ".." in relative.parts:
        raise GitError(f"code path must be inside the repository: {path}")
    if path.endswith(".py"):
        return f":(top,literal){relative}"
    return ":(top)*.py" if str(relative) == "." else f":(top){relative}/*.py"


def _add_range(
    ranges: list[tuple[int, int]], start: bytes, count: bytes | None
) -> None:
    length = 1 if count is None else int(count)
    if length:
        ranges.append((int(start), int(start) + length - 1))


def resolve_commit(repo: Path, revision: str) -> str:
    _git(repo, "rev-parse", "--git-dir")
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
