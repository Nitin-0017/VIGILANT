from pathlib import Path

import pytest

from vigilant.github_summary import MAX_SUMMARY_BYTES, render_summary

BASE = "a" * 40
HEAD = "b" * 40
REPOSITORY = "owner/test-repo"


def finding(**overrides):
    return {
        "path": "src/db.py",
        "function": "connect",
        "old_line": 4,
        "new_line": 6,
        "changes": ["timeout: 30 -> 60"],
        "source_verified": True,
        "base_location": {
            "commit": BASE,
            "path": "src/db.py",
            "start_line": 4,
            "end_line": 5,
            "changed_lines": [[4, 4]],
        },
        "head_location": {
            "commit": HEAD,
            "path": "src/db.py",
            "start_line": 6,
            "end_line": 7,
            "changed_lines": [[6, 6]],
        },
        "documentation_commit": HEAD,
        "verdict": "drift",
        "model_verdict": "drift",
        "evidence": [
            {
                "path": "docs/my guide.md",
                "heading": "Connecting",
                "line": 9,
                "end_line": 10,
                "text": "`connect` waits 30\nseconds.",
            }
        ],
        "code_evidence": "def connect(timeout=60):",
        "code_location": {
            "side": "head",
            "line": 6,
            "end_line": 6,
            "text": "def connect(timeout=60):",
        },
        "explanation": "The default changed.",
        "review_reason": None,
    } | overrides


def report(*findings, **overrides):
    counts = {}
    for item in findings:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    return {
        "status": "completed",
        "error": None,
        "base_commit": BASE,
        "head_commit": HEAD,
        "skipped_files": [],
        "excluded_files": [],
        "notice": "Not production-proven.",
        "summary": counts,
        "findings": list(findings),
    } | overrides


def test_finding_shows_permanent_links_pinned_to_the_right_commits():
    text = render_summary(report(finding()), REPOSITORY)

    assert f"https://github.com/owner/test-repo/blob/{HEAD}/src/db.py#L6-L7" in text
    assert f"https://github.com/owner/test-repo/blob/{BASE}/src/db.py#L4-L5" in text
    assert f"https://github.com/owner/test-repo/blob/{HEAD}/src/db.py#L6)" in text
    assert f"blob/{HEAD}/docs/my%20guide.md#L9-L10" in text
    assert f"https://github.com/owner/test-repo/commit/{BASE}" in text
    assert f"https://github.com/owner/test-repo/commit/{HEAD}" in text
    assert "changed lines 6" in text
    assert "changed lines 4" in text


def test_finding_shows_verdict_evidence_section_and_explanation():
    text = render_summary(report(finding()), REPOSITORY)

    assert "### connect: drift" in text
    assert "```\n`connect` waits 30\nseconds.\n```" in text
    assert "```\ndef connect(timeout=60):\n```" in text
    assert "section Connecting" in text
    assert "The default changed." in text
    assert "Not production\\-proven." in text


def test_every_verdict_is_counted_and_preserved():
    items = [
        finding(verdict=verdict, function=f"f_{verdict}")
        for verdict in ("drift", "missing_documentation", "no_drift", "needs_review")
    ]

    text = render_summary(report(*items), REPOSITORY)

    for label in ("drift", "missing\\_documentation", "no\\_drift", "needs\\_review"):
        assert f"| {label} | 1 |" in text
    assert text.index("f\\_drift") < text.index("f\\_needs\\_review")
    assert text.index("f\\_needs\\_review") < text.index("f\\_no\\_drift")


def test_downgraded_finding_shows_reason_and_original_verdict():
    item = finding(
        verdict="needs_review",
        model_verdict="no_drift",
        evidence=[],
        code_location=None,
        explanation="",
        review_reason="the code evidence is not in the changed code",
    )

    text = render_summary(report(item), REPOSITORY)

    assert "**Review reason:** the code evidence is not in the changed code" in text
    assert "**Model verdict before checks:** no\\_drift" in text
    assert "Documentation evidence" not in text


def test_unverified_source_has_no_line_links():
    item = finding(
        source_verified=False,
        base_location=None,
        head_location=None,
        verdict="needs_review",
        evidence=[],
        code_location=None,
    )

    text = render_summary(report(item), REPOSITORY)

    assert "line references unverified" in text
    assert "/blob/" not in text


def test_deleted_function_links_only_to_the_base_commit():
    item = finding(head_location=None, evidence=[], code_location=None)

    text = render_summary(report(item), REPOSITORY)

    assert f"blob/{BASE}/src/db.py#L4-L5" in text
    assert f"blob/{HEAD}/src/db.py" not in text


def test_base_side_code_evidence_links_to_the_base_commit():
    item = finding(
        code_location={"side": "base", "line": 5, "end_line": 5, "text": "log(a)"},
    )

    text = render_summary(report(item), REPOSITORY)

    assert f"blob/{BASE}/src/db.py#L5)" in text


def test_invalid_commit_is_never_linked():
    item = finding(
        evidence=[],
        code_location=None,
        head_location=finding()["head_location"] | {"commit": "not-a-sha"},
    )

    text = render_summary(report(item), REPOSITORY)

    assert "blob/not-a-sha" not in text


def test_no_changed_functions_says_so():
    text = render_summary(report(), REPOSITORY)

    assert "No changed Python functions were analyzed." in text


def test_skipped_and_excluded_files_are_disclosed():
    text = render_summary(
        report(
            finding(),
            skipped_files=[{"path": "bad.py", "reason": "invalid Python"}],
            excluded_files=["tests/a.py"],
        ),
        REPOSITORY,
    )

    assert "**Not analyzed:** bad.py: invalid Python" in text
    assert "**Outside the configured code paths:** tests/a.py" in text


def test_failed_scan_is_explicit_and_hides_the_home_directory():
    text = render_summary(
        {
            "status": "failed",
            "error": f"model file not found: {Path.home()}/models/a.gguf",
            "findings": [],
        },
        REPOSITORY,
    )

    assert "**Scan failed. No verdicts were produced for this run.**" in text
    assert "not a drift result" in text
    assert "model file not found: ~/models/a.gguf" in text
    assert str(Path.home()) not in text
    assert "| Verdict |" not in text


def test_cancelled_scan_is_explicit():
    text = render_summary({"status": "cancelled", "findings": []}, REPOSITORY)

    assert "**Scan cancelled.** No verdicts were produced" in text


def test_unknown_status_is_not_presented_as_success():
    text = render_summary({"status": "unexpected", "findings": []}, REPOSITORY)

    assert "**Scan failed.**" in text


@pytest.mark.parametrize(
    "unsafe, forbidden",
    [
        ("<img src=x onerror=alert(1)>", "<img"),
        ("[click](https://evil.example)", "[click](https"),
        ("| fake | table |", "| fake | table |"),
        ("**bold**", "**bold**"),
        ("<!-- hidden -->", "<!--"),
        ("&lt;script&gt;", "<script"),
    ],
)
def test_repository_and_model_text_cannot_inject_markup(unsafe, forbidden):
    item = finding(function=unsafe, explanation=unsafe, changes=[unsafe])

    text = render_summary(report(item), REPOSITORY)

    assert forbidden not in text


def test_mentions_and_control_characters_are_neutralized():
    item = finding(explanation="Ping @someone \x1b[31mnow")

    text = render_summary(report(item), REPOSITORY)

    assert "@someone" not in text
    assert "@​someone" in text
    assert "\x1b" not in text


def test_backticks_in_a_snippet_cannot_escape_its_code_fence():
    snippet = "text\n```\n# Injected heading\n```"
    item = finding(
        evidence=[
            {"path": "a.md", "heading": None, "line": 1, "end_line": 4, "text": snippet}
        ]
    )

    text = render_summary(report(item), REPOSITORY)

    assert f"````\n{snippet}\n````" in text
    assert "section \\(no heading\\)" in text


def test_oversized_summary_stays_under_the_limit_and_says_what_was_omitted():
    big = "x" * 300_000
    items = [
        finding(
            function=f"f{index}",
            evidence=[
                {"path": "a.md", "heading": "H", "line": 1, "end_line": 1, "text": big}
            ],
        )
        for index in range(5)
    ]

    text = render_summary(report(*items), REPOSITORY)

    assert len(text.encode()) <= MAX_SUMMARY_BYTES + 200
    assert "**3 finding(s) omitted**" in text
    assert "| drift | 5 |" in text


@pytest.mark.parametrize("name", ["owner", "a/b/c", "a b/c", "owner/repo)](x"])
def test_invalid_repository_name_is_rejected(name):
    with pytest.raises(ValueError, match="invalid repository name"):
        render_summary(report(), name)


def test_location_labels_read_correctly_and_each_source_is_its_own_paragraph():
    text = render_summary(report(finding()), REPOSITORY)

    assert "src/db.py line 6](" in text
    assert "src/db.py lines 6-7](" in text
    assert "changed lines 6\n\n**Source (base):**" in text
