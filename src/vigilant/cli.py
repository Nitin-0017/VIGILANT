import argparse
import json
import os
import signal
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
from itertools import groupby
from pathlib import Path

from vigilant.ast_diff import (
    ChangedFunction,
    FunctionChange,
    UnparsableSource,
    changed_functions,
    function_changes,
)
from vigilant.drift import Finding, ScanSettings, detect_drift
from vigilant.git_diff import (
    GitError,
    changed_python_files,
    excluded_python_paths,
    markdown_files,
    resolve_commit,
)
from vigilant.github_summary import render_summary
from vigilant.local_models import ModelExecutionError
from vigilant.locations import verify_findings
from vigilant.markdown_docs import Section, extract_sections

MODEL_NOTICE = (
    "These verdicts come from local AI models. Qwen2.5-Coder-7B-Instruct is a selected "
    "candidate, not a production-proven model, so check its evidence before acting."
)


class ScanCancelled(Exception):
    def __init__(self, signal_number: int):
        super().__init__(f"cancelled by signal {signal_number}")
        self.signal_number = signal_number


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vigilant",
        description="Detect documentation drift caused by code changes.",
    )
    parser.add_argument(
        "--version", action="version", version=f"vigilant {version('vigilant')}"
    )
    subparsers = parser.add_subparsers(dest="command")

    defaults = ScanSettings()
    scan_parser = subparsers.add_parser(
        "scan",
        help="detect documentation drift between two revisions using local AI models",
        description=(
            "Detect documentation drift between two Git revisions of the repository "
            "in the current directory, using local Qwen models through llama-server. "
            "Exits with 0 when the scan completes, whatever the verdicts, and 1 on "
            "any error. No other detection method replaces a model that fails."
        ),
    )
    _add_revision_arguments(scan_parser)
    scan_parser.add_argument(
        "--coder-model", type=Path, default=defaults.coder_model, help="reasoning GGUF"
    )
    scan_parser.add_argument(
        "--embedding-model",
        type=Path,
        default=defaults.embedding_model,
        help="embedding GGUF",
    )
    scan_parser.add_argument("--llama-server", default=defaults.llama_server)
    scan_parser.add_argument(
        "--top-k",
        type=int,
        default=defaults.top_k,
        help="documentation sections retrieved per function",
    )
    scan_parser.add_argument("--ctx-size", type=int, default=defaults.ctx_size)
    scan_parser.add_argument(
        "--embedding-ctx-size", type=int, default=defaults.embedding_ctx_size
    )
    scan_parser.add_argument("--max-tokens", type=int, default=defaults.max_tokens)
    scan_parser.add_argument("--gpu-layers", type=int, default=defaults.gpu_layers)
    scan_parser.add_argument("--threads", type=int, default=defaults.threads)
    scan_parser.add_argument("--format", choices=["text", "json"], default="text")
    scan_parser.add_argument(
        "--code-path",
        action="append",
        default=[],
        help="only analyze Python files under this repository path; repeatable",
    )
    scan_parser.add_argument(
        "--report-file",
        type=Path,
        help="also write a JSON report here, including failed and cancelled scans",
    )

    changes_parser = subparsers.add_parser(
        "changes",
        help="list Python function signature changes without any AI detection",
        description=(
            "List Python function signature changes between two Git revisions of the "
            "repository in the current directory. This is structural information, "
            "not drift detection. Exits with 0 when it completes and 1 on error."
        ),
    )
    _add_revision_arguments(changes_parser)

    summary_parser = subparsers.add_parser(
        "github-summary",
        help="render a scan report file as a GitHub Actions job summary",
        description=(
            "Print a scan report file as Markdown for a GitHub Actions job summary. "
            "Exits with 1 if the report cannot be read."
        ),
    )
    summary_parser.add_argument(
        "report", type=Path, help="report from scan --report-file"
    )
    summary_parser.add_argument(
        "--repository", required=True, help="GitHub repository, as owner/name"
    )

    args = parser.parse_args()
    if args.command == "scan":
        return _scan(args)
    if args.command == "changes":
        return _changes(args.base, args.target)
    if args.command == "github-summary":
        return _github_summary(args.report, args.repository)
    parser.print_help()
    return 0


def _add_revision_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("base", help="revision to compare from")
    parser.add_argument(
        "target",
        nargs="?",
        default="HEAD",
        help="revision to compare to (default: HEAD)",
    )


def _scan(args: argparse.Namespace) -> int:
    settings = ScanSettings(
        coder_model=args.coder_model,
        embedding_model=args.embedding_model,
        llama_server=args.llama_server,
        top_k=args.top_k,
        ctx_size=args.ctx_size,
        embedding_ctx_size=args.embedding_ctx_size,
        max_tokens=args.max_tokens,
        gpu_layers=args.gpu_layers,
        threads=args.threads,
    )
    signal.signal(signal.SIGINT, _cancel_scan)
    signal.signal(signal.SIGTERM, _cancel_scan)
    repo = Path.cwd()
    code_paths = tuple(args.code_path)
    try:
        base_commit = resolve_commit(repo, args.base)
        head_commit = resolve_commit(repo, args.target)
        files = changed_python_files(repo, base_commit, head_commit, code_paths)
        excluded = excluded_python_paths(repo, base_commit, head_commit, code_paths)
        functions, skipped = _changed_functions(files)
        sections = _documentation_sections(head_commit)
        findings = detect_drift(functions, sections, settings, _print_progress)
        findings = verify_findings(repo, base_commit, head_commit, findings)
    except (GitError, ModelExecutionError) as error:
        print(f"vigilant: error: {error}", file=sys.stderr)
        return _finish_unsuccessful(args.report_file, "failed", str(error), 1)
    except ScanCancelled as cancelled:
        print("vigilant: scan cancelled", file=sys.stderr)
        code = 128 + cancelled.signal_number
        return _finish_unsuccessful(args.report_file, "cancelled", None, code)

    report = _scan_report(findings, base_commit, head_commit, skipped, excluded)
    if args.report_file and not _write_report(args.report_file, report):
        return 1
    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_findings(findings)
    return 0


def _cancel_scan(signal_number: int, frame: object) -> None:
    # Cleanup below must finish even if the runner signals again.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise ScanCancelled(signal_number)


def _finish_unsuccessful(
    report_file: Path | None, status: str, error: str | None, code: int
) -> int:
    if report_file:
        _write_report(report_file, {"status": status, "error": error, "findings": []})
    return code


def _write_report(path: Path, report: dict) -> bool:
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2, default=str) + "\n")
        os.replace(temporary, path)
    except OSError as error:
        print(f"vigilant: error: cannot write report: {error}", file=sys.stderr)
        return False
    return True


def _github_summary(report_file: Path, repository: str) -> int:
    try:
        report = json.loads(report_file.read_text())
        print(render_summary(report, repository), end="")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"vigilant: error: cannot render report: {error}", file=sys.stderr)
        return 1
    return 0


def _changed_functions(files: list) -> tuple[list[ChangedFunction], list[dict]]:
    functions = []
    skipped = []
    for file in files:
        try:
            functions += changed_functions(file)
        except UnparsableSource as error:
            print(f"vigilant: warning: skipped {file.path}: {error}", file=sys.stderr)
            skipped.append({"path": file.path, "reason": str(error)})
    return functions, skipped


def _documentation_sections(revision: str) -> list[Section]:
    return [
        section
        for path, source in markdown_files(Path.cwd(), revision)
        for section in extract_sections(path, source)
    ]


def _print_progress(message: str) -> None:
    print(f"vigilant: {_printable(message)}", file=sys.stderr)


def _scan_report(
    findings: list[Finding],
    base_commit: str,
    head_commit: str,
    skipped: list[dict],
    excluded: list[str],
) -> dict:
    return {
        "status": "completed",
        "error": None,
        "base_commit": base_commit,
        "head_commit": head_commit,
        "skipped_files": skipped,
        "excluded_files": excluded,
        "notice": MODEL_NOTICE,
        "summary": Counter(f.assessment.verdict for f in findings),
        "findings": [
            {
                "path": f.function.path,
                "function": f.function.function,
                "old_line": f.function.old_line,
                "new_line": f.function.new_line,
                "changes": [change.detail for change in f.function.changes],
                "source_verified": f.source_verified,
                "base_location": asdict(f.base_location) if f.base_location else None,
                "head_location": asdict(f.head_location) if f.head_location else None,
                "documentation_commit": head_commit,
                **asdict(f.assessment),
                "retrieved": [
                    {
                        "path": r.section.path,
                        "heading": r.section.heading,
                        "start_line": r.section.start_line,
                        "end_line": r.section.end_line,
                        "score": round(r.score, 4),
                    }
                    for r in f.retrieved
                ],
            }
            for f in findings
        ],
    }


def _print_findings(findings: list[Finding]) -> None:
    if not findings:
        print("No changed Python functions found.")
        return
    print(f"{MODEL_NOTICE}\n")
    for finding in findings:
        function = finding.function
        assessment = finding.assessment
        line = function.new_line or function.old_line
        print(f"{function.path}:{line} {function.function}: {assessment.verdict}")
        for change in function.changes:
            print(f"  change: {_printable(change.detail)}")
        for evidence in assessment.evidence:
            print(
                f"  documentation {evidence.path}:{evidence.line}: "
                f"{_printable(evidence.text)}"
            )
        if assessment.code_evidence:
            print(f"  code: {_printable(assessment.code_evidence)}")
        if assessment.explanation and assessment.verdict != "needs_review":
            print(f"  explanation: {_printable(assessment.explanation)}")
        if assessment.review_reason:
            print(f"  review reason: {_printable(assessment.review_reason)}")
    counts = Counter(finding.assessment.verdict for finding in findings)
    summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    print(f"\n{len(findings)} changed function(s): {summary}.")


def _printable(text: str) -> str:
    """Drop control characters, because this text comes from a repository or a model."""
    return "".join(
        character
        for character in text.replace("\n", " ")
        if not unicodedata.category(character).startswith("C")
    )


def _changes(base: str, target: str) -> int:
    try:
        files = changed_python_files(Path.cwd(), base, target)
    except GitError as error:
        print(f"vigilant: error: {error}", file=sys.stderr)
        return 1

    change_count = 0
    for file in files:
        try:
            changes = function_changes(file)
        except UnparsableSource as error:
            print(f"vigilant: warning: skipped {file.path}: {error}", file=sys.stderr)
            continue
        change_count += len(changes)
        if changes:
            print(f"{file.path} ({file.status})")
            _print_changes(changes)

    if change_count:
        print(f"{change_count} function signature change(s) found.")
    else:
        print("No function signature changes found.")
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
