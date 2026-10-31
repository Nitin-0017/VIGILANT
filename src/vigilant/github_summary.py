import html
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

MAX_SUMMARY_BYTES = 900_000
SHA = re.compile(r"[0-9a-f]{40,64}")
REPOSITORY = re.compile(r"[\w.-]+/[\w.-]+")
MARKDOWN_SYNTAX = re.compile(r"([\\`*_{}\[\]()<>#+\-!|~:])")
VERDICT_ORDER = ("drift", "missing_documentation", "needs_review", "no_drift")


def render_summary(report: dict, repository: str) -> str:
    """Render a scan report as Markdown; repository text is never trusted as markup."""
    if not REPOSITORY.fullmatch(repository):
        raise ValueError(f"invalid repository name: {repository}")

    status = report.get("status")
    header = ["## VIGILANT documentation drift report", ""]
    if status == "failed":
        return _join(header + _failed_lines(report))
    if status == "cancelled":
        return _join(
            header + ["**Scan cancelled.** No verdicts were produced for this run."]
        )
    if status != "completed":
        return _join(header + ["**Scan failed.** The report status is not recognized."])

    lines = header + [_text(report["notice"]), ""] + _overview(report, repository)
    used = len(_join(lines).encode())
    findings = sorted(
        report["findings"],
        key=lambda f: (VERDICT_ORDER.index(f["verdict"]), f["path"], f["function"]),
    )
    omitted = 0
    for finding in findings:
        block = _finding_lines(finding, repository)
        size = len(_join(block).encode())
        if omitted or used + size > MAX_SUMMARY_BYTES:
            omitted += 1
            continue
        lines += block
        used += size
    if omitted:
        lines += [
            f"**{omitted} finding(s) omitted** because this summary reached its size "
            "limit. The verdict counts above include them.",
        ]
    return _join(lines)


def _failed_lines(report: dict) -> list[str]:
    error = _printable(report.get("error") or "unknown error")
    error = error.replace(str(Path.home()), "~")
    return [
        "**Scan failed. No verdicts were produced for this run.**",
        "",
        "This is an execution error, not a drift result.",
        "",
        *_fenced(error),
    ]


def _overview(report: dict, repository: str) -> list[str]:
    base = report["base_commit"]
    head = report["head_commit"]
    lines = [
        f"Compared {_commit_link(repository, base)} with "
        f"{_commit_link(repository, head)}.",
        "",
    ]
    if not report["findings"]:
        lines += ["No changed Python functions were analyzed.", ""]
    else:
        lines += ["| Verdict | Functions |", "| --- | --- |"]
        counts = report["summary"]
        lines += [f"| {_text(v)} | {counts.get(v, 0)} |" for v in VERDICT_ORDER]
        lines.append("")
    for item in report.get("skipped_files", []):
        lines.append(
            f"**Not analyzed:** {_text(item['path'])}: {_text(item['reason'])}"
        )
    if excluded := report.get("excluded_files"):
        names = ", ".join(_text(path) for path in excluded)
        lines.append(f"**Outside the configured code paths:** {names}")
    return lines + [""]


def _finding_lines(finding: dict, repository: str) -> list[str]:
    lines = [
        f"### {_text(finding['function'])}: {_text(finding['verdict'])}",
        "",
    ]
    if finding["changes"]:
        details = "; ".join(_text(change) for change in finding["changes"])
        lines += [f"**Detected change:** {details}", ""]

    lines += _source_lines(finding, repository)
    if location := finding.get("code_location"):
        commit = (finding.get("head_location") or {}).get("commit")
        if location["side"] == "base":
            commit = (finding.get("base_location") or {}).get("commit")
        link = _file_link(
            repository, commit, finding["path"], location["line"], location["end_line"]
        )
        lines += [f"**Code evidence ({location['side']}):** {link}", ""]
        lines += _fenced(location["text"]) + [""]

    for evidence in finding.get("evidence", []):
        commit = (finding.get("head_location") or {}).get("commit")
        commit = commit or finding.get("documentation_commit")
        link = _file_link(
            repository,
            commit,
            evidence["path"],
            evidence["line"],
            evidence["end_line"],
        )
        section = _text(evidence["heading"] or "(no heading)")
        lines += [f"**Documentation evidence:** {link}, section {section}", ""]
        lines += _fenced(evidence["text"]) + [""]

    if finding.get("explanation") and finding["verdict"] != "needs_review":
        lines += [f"**Explanation:** {_text(finding['explanation'])}", ""]
    if finding.get("review_reason"):
        lines += [f"**Review reason:** {_text(finding['review_reason'])}", ""]
    if finding.get("model_verdict") not in (None, finding["verdict"]):
        lines += [
            f"**Model verdict before checks:** {_text(finding['model_verdict'])}",
            "",
        ]
    return lines


def _source_lines(finding: dict, repository: str) -> list[str]:
    if not finding.get("source_verified", False):
        return [
            f"**Source:** {_text(finding['path'])} (line references unverified)",
            "",
        ]

    lines = []
    for side in ("head", "base"):
        location = finding.get(f"{side}_location")
        if location is None:
            continue
        link = _file_link(
            repository,
            location["commit"],
            location["path"],
            location["start_line"],
            location["end_line"],
        )
        changed = ", ".join(_range(*item) for item in location["changed_lines"])
        suffix = f", changed lines {changed}" if changed else ""
        lines += [f"**Source ({side}):** {link}{suffix}", ""]
    return lines


def _file_link(
    repository: str, commit: str | None, path: str, start: int, end: int
) -> str:
    noun = "line" if start == end else "lines"
    label = f"{_text(path)} {noun} {_range(start, end)}"
    if not commit or not SHA.fullmatch(commit):
        return label
    fragment = f"L{start}" if start == end else f"L{start}-L{end}"
    url = (
        f"https://github.com/{repository}/blob/{commit}/"
        f"{quote(path, safe='/')}#{fragment}"
    )
    return f"[{label}]({url})"


def _commit_link(repository: str, commit: str) -> str:
    if not SHA.fullmatch(commit or ""):
        return _text(str(commit))
    return f"[`{commit[:12]}`](https://github.com/{repository}/commit/{commit})"


def _range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _text(value: str) -> str:
    """Show repository or model text literally, including anything that looks like markup."""
    text = html.unescape(_printable(value)).replace("\n", " ")
    text = html.escape(text, quote=False)
    text = MARKDOWN_SYNTAX.sub(r"\\\1", text)
    return text.replace("@", "@​")


def _fenced(text: str) -> list[str]:
    clean = _printable(text)
    longest = max((len(run) for run in re.findall(r"`+", clean)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, clean, fence]


def _printable(text: str) -> str:
    return "".join(
        character
        for character in text
        if character == "\n" or not unicodedata.category(character).startswith("C")
    )


def _join(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"
