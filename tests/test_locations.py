import json
from dataclasses import replace

from vigilant.ast_diff import changed_functions
from vigilant.drift import Finding
from vigilant.git_diff import changed_python_files
from vigilant.locations import verify_findings
from vigilant.markdown_docs import extract_sections
from vigilant.reasoning import validate_response

DOC = "# Guide\n\nIntro text.\n\n## Connecting\n\nFirst sentence.\n\n`connect` waits 30\nseconds by default.\n"


def answer(verdict, quote, code, section=2):
    return json.dumps(
        {
            "verdict": verdict,
            "doc_evidence": [{"section": section, "quote": quote}],
            "code_evidence": code,
            "explanation": "Because.",
        }
    )


def findings_for(repo, base, head, responses):
    """Run the real validation and location code on real commits, with scripted answers."""
    sections = []
    for path in repo.path.rglob("*.md"):
        relative = path.relative_to(repo.path).as_posix()
        sections += extract_sections(relative, path.read_bytes())
    functions = []
    for file in changed_python_files(repo.path, base, head):
        functions += changed_functions(file)
    findings = [
        Finding(
            function,
            validate_response(responses.get(function.function, ""), function, sections),
        )
        for function in functions
    ]
    return verify_findings(repo.path, base, head, findings)


def test_changed_and_unchanged_lines_use_real_positions_in_each_commit(git_repo):
    old = "# Header\n\n\ndef connect(host, timeout=30):\n    return host\n"
    new = (
        "# Header\n# New comment\n# Another\n\n\n"
        "def connect(host, timeout=60):\n    return host\n"
    )
    base = git_repo.commit({"db.py": old, "docs.md": DOC})
    head = git_repo.commit({"db.py": new})

    finding = findings_for(
        git_repo,
        base,
        head,
        {
            "connect": answer(
                "drift",
                "`connect` waits 30 seconds by default.",
                "def connect(host, timeout=60):",
            )
        },
    )[0]

    assert finding.assessment.verdict == "drift"
    assert finding.source_verified is True
    assert finding.base_location.commit == base
    assert (finding.base_location.start_line, finding.base_location.end_line) == (4, 5)
    assert finding.base_location.changed_lines == ((4, 4),)
    assert finding.head_location.commit == head
    assert (finding.head_location.start_line, finding.head_location.end_line) == (6, 7)
    assert finding.head_location.changed_lines == ((6, 6),)

    code = finding.assessment.code_location
    assert (code.side, code.line, code.end_line) == ("head", 6, 6)
    assert code.text == "def connect(host, timeout=60):"

    documentation = finding.assessment.evidence[0]
    assert documentation.path == "docs.md"
    assert documentation.heading == "Connecting"
    assert (documentation.line, documentation.end_line) == (9, 10)
    assert documentation.text == "`connect` waits 30\nseconds by default."


def test_unchanged_documentation_is_verified_at_the_head_commit(git_repo):
    base = git_repo.commit({"a.py": "def f(a=1):\n    pass\n", "docs.md": DOC})
    head = git_repo.commit({"a.py": "def f(a=2):\n    pass\n"})

    finding = findings_for(
        git_repo,
        base,
        head,
        {"f": answer("drift", "First sentence.", "def f(a=2):")},
    )[0]

    assert finding.assessment.verdict == "drift"
    assert finding.assessment.evidence[0].line == 7


def test_several_findings_in_one_file_keep_separate_locations(git_repo):
    old = "def f(a=1):\n    pass\n\n\ndef g(b=1):\n    pass\n"
    new = "def f(a=2):\n    pass\n\n\ndef g(b=2):\n    pass\n"
    base = git_repo.commit({"a.py": old, "docs.md": DOC})
    head = git_repo.commit({"a.py": new})

    findings = findings_for(
        git_repo,
        base,
        head,
        {
            "f": answer("drift", "First sentence.", "def f(a=2):"),
            "g": answer("drift", "Intro text.", "def g(b=2):", section=1),
        },
    )

    assert [f.head_location.changed_lines for f in findings] == [((1, 1),), ((5, 5),)]
    assert [f.assessment.code_location.line for f in findings] == [1, 5]
    assert [f.assessment.evidence[0].line for f in findings] == [7, 3]


def test_deleted_lines_are_located_only_in_the_base_commit(git_repo):
    base = git_repo.commit(
        {"a.py": "def f(a):\n    log(a)\n    return a\n", "docs.md": DOC}
    )
    head = git_repo.commit({"a.py": "def f(a):\n    return a\n"})

    finding = findings_for(
        git_repo, base, head, {"f": answer("drift", "First sentence.", "log(a)")}
    )[0]

    assert finding.base_location.changed_lines == ((2, 2),)
    assert finding.head_location.changed_lines == ()
    code = finding.assessment.code_location
    assert (code.side, code.line) == ("base", 2)


def test_removed_function_has_only_a_base_location(git_repo):
    base = git_repo.commit({"a.py": "x = 1\n\n\ndef f():\n    pass\n", "docs.md": DOC})
    head = git_repo.commit({"a.py": "x = 1\n"})

    finding = findings_for(
        git_repo, base, head, {"f": answer("drift", "First sentence.", "def f():")}
    )[0]

    assert finding.head_location is None
    assert finding.source_verified is True
    assert (finding.base_location.start_line, finding.base_location.end_line) == (4, 5)
    assert finding.base_location.commit == base


def test_renamed_function_is_one_removal_and_one_addition(git_repo):
    base = git_repo.commit({"a.py": "def old_name():\n    pass\n", "docs.md": DOC})
    head = git_repo.commit({"a.py": "\ndef new_name():\n    pass\n"})

    findings = findings_for(git_repo, base, head, {})
    by_name = {f.function.function: f for f in findings}

    assert by_name["old_name"].base_location.start_line == 1
    assert by_name["old_name"].head_location is None
    assert by_name["new_name"].head_location.start_line == 2
    assert by_name["new_name"].base_location is None


def test_decorators_and_methods_map_to_real_lines(git_repo):
    old = "class C:\n    @staticmethod\n    def m():\n        return 1\n"
    new = "class C:\n    @staticmethod\n    def m():\n        return 2\n"
    base = git_repo.commit({"a.py": old, "docs.md": DOC})
    head = git_repo.commit({"a.py": new})

    finding = findings_for(
        git_repo, base, head, {"C.m": answer("drift", "First sentence.", "return 2")}
    )[0]

    assert (finding.head_location.start_line, finding.head_location.end_line) == (2, 4)
    assert finding.head_location.changed_lines == ((4, 4),)
    assert finding.assessment.code_location.line == 4


def test_crlf_documentation_keeps_correct_lines(git_repo):
    doc = "# A\r\n\r\nFirst.\r\n\r\n## B\r\n\r\nSecond line.\r\n"
    base = git_repo.commit({"a.py": "def f(a=1):\n    pass\n", "c.md": doc})
    head = git_repo.commit({"a.py": "def f(a=2):\n    pass\n"})

    finding = findings_for(
        git_repo,
        base,
        head,
        {"f": answer("drift", "Second line.", "def f(a=2):")},
    )[0]

    assert finding.assessment.verdict == "drift"
    assert finding.assessment.evidence[0].line == 7


def verified_finding(git_repo):
    base = git_repo.commit({"a.py": "def f(a=1):\n    pass\n", "docs.md": DOC})
    head = git_repo.commit({"a.py": "def f(a=2):\n    pass\n"})
    finding = findings_for(
        git_repo, base, head, {"f": answer("drift", "First sentence.", "def f(a=2):")}
    )[0]
    assert finding.assessment.verdict == "drift"
    return base, head, finding


def test_documentation_line_that_does_not_match_the_file_needs_review(git_repo):
    base, head, finding = verified_finding(git_repo)
    wrong = replace(finding.assessment.evidence[0], line=3, end_line=3)
    tampered = replace(
        finding, assessment=replace(finding.assessment, evidence=(wrong,))
    )

    result = verify_findings(git_repo.path, base, head, [tampered])[0]

    assert result.assessment.verdict == "needs_review"
    assert result.assessment.model_verdict == "drift"
    assert result.assessment.evidence == ()
    assert "could not be verified" in result.assessment.review_reason


def test_documentation_line_past_the_end_of_the_file_needs_review(git_repo):
    base, head, finding = verified_finding(git_repo)
    wrong = replace(finding.assessment.evidence[0], line=900, end_line=900)
    tampered = replace(
        finding, assessment=replace(finding.assessment, evidence=(wrong,))
    )

    result = verify_findings(git_repo.path, base, head, [tampered])[0]

    assert result.assessment.verdict == "needs_review"


def test_missing_documentation_file_needs_review(git_repo):
    base, head, finding = verified_finding(git_repo)
    wrong = replace(finding.assessment.evidence[0], path="absent.md")
    tampered = replace(
        finding, assessment=replace(finding.assessment, evidence=(wrong,))
    )

    result = verify_findings(git_repo.path, base, head, [tampered])[0]

    assert result.assessment.verdict == "needs_review"


def test_code_location_that_does_not_match_the_file_needs_review(git_repo):
    base, head, finding = verified_finding(git_repo)
    wrong = replace(finding.assessment.code_location, line=2, end_line=2)
    tampered = replace(
        finding, assessment=replace(finding.assessment, code_location=wrong)
    )

    result = verify_findings(git_repo.path, base, head, [tampered])[0]

    assert result.assessment.verdict == "needs_review"
    assert result.assessment.code_location is None


def test_function_lines_that_do_not_match_the_file_lose_their_location(git_repo):
    base, head, finding = verified_finding(git_repo)
    moved = replace(finding.function, new_first_line=50, new_end_line=51)

    result = verify_findings(
        git_repo.path, base, head, [replace(finding, function=moved)]
    )[0]

    assert result.source_verified is False
    assert result.head_location is None
    assert result.assessment.verdict == "needs_review"


def test_needs_review_without_evidence_keeps_its_source_location(git_repo):
    base = git_repo.commit({"a.py": "def f(a=1):\n    pass\n", "docs.md": DOC})
    head = git_repo.commit({"a.py": "def f(a=2):\n    pass\n"})
    reply = json.dumps(
        {
            "verdict": "needs_review",
            "doc_evidence": [],
            "code_evidence": "",
            "explanation": "No section is relevant.",
        }
    )

    finding = findings_for(git_repo, base, head, {"f": reply})[0]

    assert finding.assessment.verdict == "needs_review"
    assert finding.assessment.review_reason == "No section is relevant."
    assert finding.source_verified is True
    assert finding.head_location.start_line == 1
