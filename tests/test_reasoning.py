import hashlib
import json
import time

from vigilant.ast_diff import ChangedFunction
from vigilant.markdown_docs import extract_sections
from vigilant.reasoning import (
    SYSTEM_PROMPT,
    VERDICTS,
    _find_lines,
    build_messages,
    validate_response,
)

FUNCTION = ChangedFunction(
    path="db.py",
    function="connect",
    changes=(),
    old_code="def connect(timeout=30):\n    pass",
    new_code="def connect(timeout=60):\n    pass",
    old_line=1,
    new_line=1,
)
SECTIONS = extract_sections(
    "docs/db.md",
    b"# Intro\nHello.\n\n# Connecting\n\nFirst line.\n"
    b"`connect` times out\nafter 30 seconds.\n",
)


def answer(verdict="drift", doc=None, code="def connect(timeout=60):", explanation="x"):
    return json.dumps(
        {
            "verdict": verdict,
            "doc_evidence": doc if doc is not None else [],
            "code_evidence": code,
            "explanation": explanation,
        }
    )


def quote(text, section=2):
    return [{"section": section, "quote": text}]


def test_valid_evidence_keeps_the_verdict_and_points_to_the_source_line():
    result = validate_response(
        answer(doc=quote("`connect` times out after 30 seconds.")), FUNCTION, SECTIONS
    )

    assert result.verdict == "drift"
    assert result.review_reason is None
    assert result.evidence[0].path == "docs/db.md"
    assert result.evidence[0].heading == "Connecting"
    assert result.evidence[0].line == 7
    assert result.evidence[0].text == "`connect` times out\nafter 30 seconds."


def test_dropped_backticks_and_spacing_do_not_fail_a_real_quote():
    result = validate_response(
        answer(doc=quote("connect  times out after 30 seconds.")), FUNCTION, SECTIONS
    )

    assert result.verdict == "drift"
    assert "`connect`" in result.evidence[0].text


def test_quote_from_the_wrong_section_needs_review():
    result = validate_response(
        answer(doc=quote("First line.", section=1)), FUNCTION, SECTIONS
    )

    assert result.verdict == "needs_review"
    assert result.model_verdict == "drift"


def test_every_quote_must_be_real():
    doc = quote("First line.") + quote("An invented sentence.")

    result = validate_response(answer(doc=doc), FUNCTION, SECTIONS)

    assert result.verdict == "needs_review"
    assert result.evidence == ()


def test_several_real_quotes_are_all_reported():
    doc = quote("First line.") + quote("Hello.", section=1)

    result = validate_response(answer(doc=doc), FUNCTION, SECTIONS)

    assert [e.heading for e in result.evidence] == ["Connecting", "Intro"]


def test_no_drift_also_requires_evidence():
    result = validate_response(answer("no_drift"), FUNCTION, SECTIONS)

    assert result.verdict == "needs_review"
    assert result.model_verdict == "no_drift"


def test_no_drift_without_code_evidence_needs_review():
    result = validate_response(
        answer("no_drift", doc=quote("First line."), code=""), FUNCTION, SECTIONS
    )

    assert result.verdict == "needs_review"
    assert result.model_verdict == "no_drift"


def test_missing_documentation_requires_evidence_of_relevance():
    without = validate_response(answer("missing_documentation"), FUNCTION, SECTIONS)
    with_quote = validate_response(
        answer("missing_documentation", doc=quote("First line.")), FUNCTION, SECTIONS
    )

    assert without.verdict == "needs_review"
    assert with_quote.verdict == "missing_documentation"


def test_model_needs_review_keeps_its_explanation_as_the_reason():
    result = validate_response(
        answer("needs_review", code="", explanation="No section is relevant."),
        FUNCTION,
        SECTIONS,
    )

    assert result.verdict == "needs_review"
    assert result.model_verdict == "needs_review"
    assert result.review_reason == "No section is relevant."


def test_code_evidence_may_quote_old_code():
    result = validate_response(
        answer(doc=quote("First line."), code="def connect(timeout=30):"),
        FUNCTION,
        SECTIONS,
    )

    assert result.verdict == "drift"


def test_malformed_field_types_are_invalid_answers():
    for broken in (
        {"verdict": "drift", "doc_evidence": "text"},
        {
            "verdict": "drift",
            "doc_evidence": [{"section": "1", "quote": "x"}],
            "code_evidence": "",
            "explanation": "",
        },
        {"verdict": "drift", "doc_evidence": [], "code_evidence": 1, "explanation": ""},
        ["drift"],
    ):
        result = validate_response(json.dumps(broken), FUNCTION, SECTIONS)

        assert result.verdict == "needs_review"
        assert result.model_verdict is None


def test_prompt_numbers_sections_and_marks_new_functions():
    added = ChangedFunction("a.py", "f", (), None, "def f(): pass", None, 1)

    system, user = build_messages(added, SECTIONS)

    assert system["content"] == SYSTEM_PROMPT
    assert "(function did not exist)" in user["content"]
    assert "Section 1 from docs/db.md" in user["content"]
    assert "Section 2 from docs/db.md" in user["content"]


def test_classification_prompt_stays_frozen():
    digest = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()

    assert digest == (
        "e92faada79520b502fc573fac34542c3d1a526b217a1aa0eda6614ef80e4c818"
    )
    assert VERDICTS == ("drift", "missing_documentation", "no_drift", "needs_review")


def test_code_evidence_records_which_commit_side_and_line_it_came_from():
    function = ChangedFunction(
        path="db.py",
        function="connect",
        changes=(),
        old_code="def connect(timeout=30):\n    pass",
        new_code="def connect(timeout=60):\n    pass",
        old_line=4,
        new_line=9,
        old_first_line=3,
        old_end_line=5,
        new_first_line=8,
        new_end_line=10,
    )

    new = validate_response(
        answer(doc=quote("First line."), code="def connect(timeout=60):"),
        function,
        SECTIONS,
    )
    old = validate_response(
        answer(doc=quote("First line."), code="timeout=30"), function, SECTIONS
    )

    assert (new.code_location.side, new.code_location.line) == ("head", 8)
    assert (old.code_location.side, old.code_location.line) == ("base", 3)


def test_code_evidence_across_both_versions_needs_review():
    quote_across = "pass def connect(timeout=60):"

    result = validate_response(
        answer(doc=quote("First line."), code=quote_across), FUNCTION, SECTIONS
    )

    assert result.verdict == "needs_review"
    assert "no single code version" in result.review_reason


def test_prompt_never_lets_unrelated_sections_become_no_drift():
    assert "no section is relevant" in SYSTEM_PROMPT
    assert "similar name, topic or number" in " ".join(SYSTEM_PROMPT.split())


def test_quote_search_returns_the_smallest_window_and_scales_to_huge_functions():
    lines = [f"    line_{number} = {number}" for number in range(5000)]
    lines[4000] = "    target = compute("
    lines[4001] = "        value)"

    started = time.perf_counter()
    window = _find_lines(lines, "target = compute( value)")
    absent = _find_lines(lines, "not present anywhere")

    assert window == (4000, 4002)
    assert absent is None
    assert time.perf_counter() - started < 5
