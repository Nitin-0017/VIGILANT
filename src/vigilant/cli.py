import argparse
import sys
from importlib.metadata import version
from itertools import groupby
from pathlib import Path

from vigilant.ast_diff import FunctionChange, UnparsableSource, function_changes
from vigilant.doc_matching import DocMatch, match_documentation
from vigilant.git_diff import GitError, changed_python_files, markdown_files
from vigilant.markdown_docs import extract_sections


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vigilant",
        description="Detect documentation drift caused by code changes.",
    )
    parser.add_argument(
        "--version", action="version", version=f"vigilant {version('vigilant')}"
    )
    subparsers = parser.add_subparsers(dest="command")
    scan_parser = subparsers.add_parser(
        "scan",
        help="report Python function signature changes between two revisions",
        description=(
            "Report Python function signature changes between two Git revisions "
            "of the repository in the current directory. Exits with 0 when the "
            "scan completes, whether or not changes are found, and 1 on error."
        ),
    )
    scan_parser.add_argument("base", help="revision to compare from")
    scan_parser.add_argument(
        "target",
        nargs="?",
        default="HEAD",
        help="revision to compare to (default: HEAD)",
    )

    args = parser.parse_args()
    if args.command == "scan":
        return _scan(args.base, args.target)
    parser.print_help()
    return 0


def _scan(base: str, target: str) -> int:
    try:
        files = changed_python_files(Path.cwd(), base, target)
        documents = markdown_files(Path.cwd(), target)
    except GitError as error:
        print(f"vigilant: error: {error}", file=sys.stderr)
        return 1

    all_changes = []
    for file in files:
        try:
            changes = function_changes(file)
        except UnparsableSource as error:
            print(f"vigilant: warning: skipped {file.path}: {error}", file=sys.stderr)
            continue
        all_changes += changes
        if changes:
            print(f"{file.path} ({file.status})")
            _print_changes(changes)

    if all_changes:
        print(f"{len(all_changes)} function signature change(s) found.")
    else:
        print("No function signature changes found.")

    sections = [
        section
        for path, source in documents
        for section in extract_sections(path, source)
    ]
    _print_doc_matches(match_documentation(all_changes, sections))
    return 0


def _print_changes(changes: list[FunctionChange]) -> None:
    for (function, line, old_signature, new_signature), group in groupby(
        changes,
        key=lambda c: (
            c.function,
            c.new_line or c.old_line,
            c.old_signature,
            c.new_signature,
        ),
    ):
        print(f"  {function} (line {line})")
        for change in group:
            print(f"    {change.change_type}: {change.detail}")
        if old_signature:
            print(f"    - {old_signature}")
        if new_signature:
            print(f"    + {new_signature}")


def _print_doc_matches(matches: list[DocMatch]) -> None:
    if not matches:
        return
    print("\nPossibly related documentation (not a drift verdict):")
    for (source_path, function), group in groupby(
        matches, key=lambda match: (match.source_path, match.function)
    ):
        print(f"  {source_path}: {function}")
        for match in group:
            section = " > ".join(match.hierarchy) or "(no heading)"
            print(
                f"    {match.doc_path}:{match.line} in {section} "
                f"(lines {match.section_start_line}-{match.section_end_line})"
            )
            print(f"      {match.reason}: {match.evidence}")
    print(f"{len(matches)} documentation reference(s) found.")
