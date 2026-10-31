import textwrap
from dataclasses import replace
from pathlib import Path

from vigilant.ast_diff import source_lines
from vigilant.drift import Finding, SourceLocation
from vigilant.git_diff import GitError, diff_line_ranges, read_file
from vigilant.markdown_docs import document_lines

LineRange = tuple[int, int]


def verify_findings(
    repo: Path, base_commit: str, head_commit: str, findings: list[Finding]
) -> list[Finding]:
    """Attach line locations read from Git and check all evidence against those files.

    A finding whose evidence cannot be confirmed loses that evidence and becomes
    needs_review, so an unverified reference is never published.
    """
    return [
        _verify_finding(repo, base_commit, head_commit, finding) for finding in findings
    ]


def _verify_finding(
    repo: Path, base_commit: str, head_commit: str, finding: Finding
) -> Finding:
    function = finding.function
    old_ranges, new_ranges = diff_line_ranges(
        repo, base_commit, head_commit, function.path
    )
    base_location = _source_location(
        repo,
        base_commit,
        function.path,
        function.old_code,
        function.old_first_line,
        function.old_end_line,
        old_ranges,
    )
    head_location = _source_location(
        repo,
        head_commit,
        function.path,
        function.new_code,
        function.new_first_line,
        function.new_end_line,
        new_ranges,
    )
    source_verified = (function.old_code is None or base_location is not None) and (
        function.new_code is None or head_location is not None
    )

    assessment = finding.assessment
    evidence_verified = (
        source_verified
        and all(
            _doc_evidence_matches(repo, head_commit, e) for e in assessment.evidence
        )
        and _code_evidence_matches(repo, base_commit, head_commit, finding)
    )
    if not evidence_verified:
        assessment = replace(
            assessment,
            verdict="needs_review",
            evidence=(),
            code_evidence="",
            code_location=None,
            explanation="",
            review_reason="the evidence could not be verified against the repository",
        )
    return replace(
        finding,
        assessment=assessment,
        base_location=base_location,
        head_location=head_location,
        source_verified=source_verified,
    )


def _source_location(
    repo: Path,
    commit: str,
    path: str,
    code: str | None,
    first_line: int | None,
    end_line: int | None,
    ranges: list[LineRange],
) -> SourceLocation | None:
    if code is None or first_line is None or end_line is None:
        return None
    lines = _python_lines(repo, commit, path)
    if lines is None or end_line > len(lines):
        return None
    if textwrap.dedent("\n".join(lines[first_line - 1 : end_line])) != code:
        return None
    return SourceLocation(
        commit=commit,
        path=path,
        start_line=first_line,
        end_line=end_line,
        changed_lines=tuple(
            (max(start, first_line), min(end, end_line))
            for start, end in ranges
            if start <= end_line and end >= first_line
        ),
    )


def _doc_evidence_matches(repo: Path, commit: str, evidence) -> bool:
    source = _read(repo, commit, evidence.path)
    if source is None:
        return False
    lines = document_lines(source)
    if evidence.line < 1 or evidence.end_line > len(lines):
        return False
    return (
        "\n".join(lines[evidence.line - 1 : evidence.end_line]).strip() == evidence.text
    )


def _code_evidence_matches(
    repo: Path, base_commit: str, head_commit: str, finding: Finding
) -> bool:
    location = finding.assessment.code_location
    if location is None:
        return not finding.assessment.code_evidence
    commit = head_commit if location.side == "head" else base_commit
    lines = _python_lines(repo, commit, finding.function.path)
    if lines is None or location.line < 1 or location.end_line > len(lines):
        return False
    text = textwrap.dedent("\n".join(lines[location.line - 1 : location.end_line]))
    return text.strip() == location.text


def _python_lines(repo: Path, commit: str, path: str) -> list[str] | None:
    source = _read(repo, commit, path)
    if source is None:
        return None
    try:
        return source_lines(source)
    except SyntaxError:
        return None


def _read(repo: Path, commit: str, path: str) -> bytes | None:
    try:
        return read_file(repo, commit, path)
    except GitError:
        return None
