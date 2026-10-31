import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from benchmarks.local_models import resource_samples
from benchmarks.metrics import (
    abstention_metrics,
    approved_dataset,
    choose_threshold,
    classification_metrics,
    dataset_fingerprint,
    evidence_is_correct,
    evidence_metrics,
    parse_response,
    retrieval_metrics,
    score_separation,
    set_retrieval_metrics,
    unsupported_claims,
    wilson_interval,
)
from benchmarks.run_reasoning import DATASET, build_messages, evaluate_case, summarize
from benchmarks.run_baseline import deterministic_rankings
from benchmarks.run_retrieval import (
    BATCH_SIZE,
    CASES,
    DATASET_DIR,
    INSTRUCTION,
    embedding_report,
    load_sections,
    query_text,
    rank_sections,
    run_sentence_transformers,
    section_id,
)
from vigilant.local_models import ModelExecutionError, llama_embeddings, llama_server
from vigilant.markdown_docs import extract_sections

DRIFT_CASES = json.loads(DATASET.read_text())
RETRIEVAL_CASES = json.loads(CASES.read_text())

CASE = {
    "id": "x",
    "category": "changed_default",
    "function": "connect",
    "change": "default_changed: timeout: 30 -> 60",
    "old_code": "def connect(timeout=30): pass",
    "new_code": "def connect(timeout=60): pass",
    "doc_path": "docs/db.md",
    "section": "## Connecting\n\nConnections time out after\n30 seconds.",
    "expected_verdict": "drift",
    "expected_doc_evidence": "Connections time out after 30 seconds.",
}

SYNTHETIC_SECTIONS = extract_sections(
    "docs/a.md", b"# First\nShort.\n# Second\nA much longer section of text.\n"
)
SYNTHETIC_QUERIES = [
    {
        "id": id_,
        "split": split,
        "category": category,
        "path": "a.py",
        "function": "f",
        "change": "changed",
        "old_code": "def f(): pass",
        "new_code": "def f(x): pass",
        "relevant": relevant,
    }
    for id_, split, category, relevant in [
        ("d1", "dev", "semantic_only", ["docs/a.md#First"]),
        ("d2", "dev", "no_documentation", []),
        ("t1", "test", "semantic_only", ["docs/a.md#Second"]),
        ("t2", "test", "no_documentation", []),
    ]
]


def answer(doc="", code="", explanation=""):
    return {
        "verdict": "drift",
        "doc_evidence": doc,
        "code_evidence": code,
        "explanation": explanation,
    }


def test_classification_metrics_counts_missing_answers_as_wrong():
    metrics = classification_metrics(
        [
            ("drift", "drift"),
            ("missing_documentation", "drift"),
            ("drift", "no_drift"),
            ("drift", None),
            ("no_drift", "drift"),
            ("no_drift", "no_drift"),
        ]
    )

    assert metrics["true_positive"] == 2
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 2
    assert metrics["precision"] == 0.667
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 0.571
    assert metrics["accuracy"] == 0.333


def test_classification_metrics_with_no_predicted_positives():
    metrics = classification_metrics([("no_drift", "no_drift")])

    assert metrics["precision"] == 0.0
    assert metrics["precision_interval"] is None
    assert metrics["f1"] == 0.0
    assert metrics["accuracy"] == 1.0


def test_wilson_interval_matches_known_value():
    assert wilson_interval(8, 10) == (0.49, 0.943)
    assert wilson_interval(0, 0) is None


def test_parse_response_rejects_invalid_or_incomplete_json():
    assert parse_response("not json") is None
    assert parse_response('{"verdict": "drift"}') is None
    assert parse_response("[1]") is None
    assert parse_response(json.dumps(answer() | {"verdict": "maybe"})) is None
    assert parse_response(json.dumps(answer() | {"doc_evidence": 7})) is None
    assert parse_response(json.dumps(answer()))["verdict"] == "drift"


def test_evidence_must_be_quoted_from_the_section_and_cover_the_label():
    full_quote = "Connections time out after 30 seconds."

    assert evidence_is_correct(CASE, answer(doc=full_quote)) is True
    assert evidence_is_correct(CASE, answer(doc="time out after 30 seconds")) is True
    assert evidence_is_correct(CASE, answer(doc="## Connecting")) is False
    assert evidence_is_correct(CASE, answer(doc="Times out after 30s.")) is False
    assert evidence_is_correct(CASE, answer()) is False


def test_evidence_rejects_fragments_and_whole_section_quotes():
    long_section = CASE | {"section": f"{CASE['section']}\n{'Other text. ' * 20}"}

    assert evidence_is_correct(CASE, answer(doc="30 seconds")) is False
    assert (
        evidence_is_correct(long_section, answer(doc=long_section["section"])) is False
    )


def test_evidence_is_not_scored_without_a_labelled_line():
    case = CASE | {"expected_doc_evidence": None}

    assert evidence_is_correct(case, answer()) is None


def test_unsupported_claims_finds_invented_quotes_and_identifiers():
    honest = answer(
        doc="Connections time out after 30 seconds.",
        code="def connect(timeout=60): pass",
        explanation="`connect()` now uses `timeout` 60, unlike `docs/db.md`.",
    )
    invented = answer(
        doc="Connections never time out.",
        code="def connect(retries=9): pass",
        explanation="`retries` changed.",
    )

    assert unsupported_claims(CASE, honest) == []
    assert unsupported_claims(CASE, invented) == [
        "doc quote: Connections never time out.",
        "code quote: def connect(retries=9): pass",
        "identifier: retries",
    ]


def test_retrieval_metrics_use_rank_of_first_relevant_section():
    metrics = retrieval_metrics(
        [
            (["a", "b"], {"a"}),
            (["x", "b", "y", "z", "c"], {"c"}),
            (["x", "y", "z", "w", "v", "c"], {"c"}),
            (["x"], set()),
        ]
    )

    assert metrics == {
        "queries": 3,
        "recall_at_1": 0.333,
        "recall_at_3": 0.333,
        "recall_at_5": 0.667,
        "mrr": 0.456,
    }


def test_set_retrieval_metrics_count_wrong_results_for_undocumented_functions():
    metrics = set_retrieval_metrics([(["a", "b"], {"a", "c"}), (["x"], set())])

    assert metrics == {"returned": 3, "precision": 0.333, "recall": 0.5}


def test_abstention_metrics_separate_false_matches_from_missed_documentation():
    metrics = abstention_metrics(
        [(True, False), (False, False), (False, False), (False, False)]
        + [(True, True), (False, True)]
    )

    assert metrics["undocumented_queries"] == 4
    assert metrics["false_positive_rate"] == 0.25
    assert metrics["abstention_rate"] == 0.75
    assert metrics["documented_queries"] == 2
    assert metrics["false_no_match_rate"] == 0.5


def test_choose_threshold_separates_scores_and_prefers_abstaining_on_ties():
    separable = [(0.9, True), (0.8, True), (0.5, False), (0.4, False)]
    overlapping = [(0.7, True), (0.7, False), (0.6, False)]

    assert choose_threshold(separable) == 0.8
    assert choose_threshold(overlapping) == 0.7


def test_score_separation_counts_documented_queries_outranking_undocumented():
    assert score_separation([(0.9, True), (0.1, False)]) == 1.0
    assert score_separation([(0.5, True), (0.5, False)]) == 0.5
    assert score_separation([(0.2, True), (0.9, False)]) == 0.0


def fake_vector(text):
    return [float(len(text)), 1.0]


def test_embedding_report_gives_every_runtime_identical_batches():
    batches = {"first": [], "second": []}

    def runtime(name):
        def embed(texts):
            batches[name].append(list(texts))
            return [fake_vector(text) for text in texts]

        return embed

    first, _ = embedding_report(runtime("first"), SYNTHETIC_QUERIES, SYNTHETIC_SECTIONS)
    second, _ = embedding_report(
        runtime("second"), SYNTHETIC_QUERIES, SYNTHETIC_SECTIONS
    )

    assert batches["first"] == batches["second"]
    assert first["inputs_sha256"] == second["inputs_sha256"]
    assert first["batch_size"] == second["batch_size"] == BATCH_SIZE
    assert max(len(batch) for batch in batches["first"]) <= BATCH_SIZE


def test_query_modes_differ_only_in_context():
    case = next(c for c in RETRIEVAL_CASES if c["id"] == "se-3")

    with_context = query_text(case, "change_context")
    name_only = query_text(case, "name_only")

    assert with_context.startswith(f"Instruct: {INSTRUCTION}\nQuery:")
    assert case["old_code"] in with_context and case["new_code"] in with_context
    assert case["change"] in with_context
    assert name_only == f"Instruct: {INSTRUCTION}\nQuery:Function cache_put"


def test_threshold_comes_from_dev_queries_and_ignores_test_scores():
    def embed(texts):
        return [fake_vector(text) for text in texts]

    report, _ = embedding_report(embed, SYNTHETIC_QUERIES, SYNTHETIC_SECTIONS)
    changed_test_query = [
        query | {"new_code": "x" * 900} if query["split"] == "test" else query
        for query in SYNTHETIC_QUERIES
    ]
    changed, _ = embedding_report(embed, changed_test_query, SYNTHETIC_SECTIONS)
    mode = report["change_context"]

    assert mode["threshold_from_dev"] == changed["change_context"]["threshold_from_dev"]
    assert mode["abstention"]["undocumented_queries"] == 1
    assert mode["abstention"]["documented_queries"] == 1
    assert {query["id"] for query in mode["queries"]} == {"t1", "t2"}


def test_missing_local_models_raise_an_explicit_error(tmp_path):
    with pytest.raises(ModelExecutionError, match="model file not found"):
        with llama_server(tmp_path / "missing.gguf", [], sys.executable):
            pass
    with pytest.raises(ModelExecutionError, match="no-such-server not found"):
        with llama_server(tmp_path / "missing.gguf", [], "no-such-server"):
            pass


def test_sentence_transformers_missing_weights_raise_an_explicit_error(tmp_path):
    args = argparse.Namespace(weights=tmp_path / "missing", device="cpu")

    with pytest.raises(ModelExecutionError, match="model directory not found"):
        run_sentence_transformers(args, [], [])


def test_server_that_fails_to_load_raises_an_explicit_error(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_text("not a model")
    server = tmp_path / "failing-server"
    server.write_text("#!/bin/sh\nexit 3\n")
    server.chmod(0o755)

    with pytest.raises(ModelExecutionError, match="exited with code 3"):
        with llama_server(model, [], str(server)):
            pass


def test_unreachable_server_raises_an_explicit_error():
    with pytest.raises(ModelExecutionError, match="inference request failed"):
        llama_embeddings("http://127.0.0.1:9", ["text"])


def test_malformed_server_responses_raise_explicit_errors(fake_server, monkeypatch):
    monkeypatch.setattr(FakeLlamaServer, "body", {"unexpected": True})

    with pytest.raises(ModelExecutionError, match="malformed chat response"):
        evaluate_case(fake_server, CASE, use_schema=True, max_tokens=50)
    with pytest.raises(ModelExecutionError, match="malformed embedding response"):
        llama_embeddings(fake_server, ["text"])


def test_server_error_status_raises_an_explicit_error(fake_server, monkeypatch):
    monkeypatch.setattr(FakeLlamaServer, "status", 500)

    with pytest.raises(ModelExecutionError, match="inference request failed"):
        evaluate_case(fake_server, CASE, use_schema=True, max_tokens=50)


def test_evidence_metrics_keep_detected_and_all_drift_denominators_apart():
    results = [
        {"expected": "drift", "predicted": "drift", "evidence_correct": True},
        {"expected": "drift", "predicted": "drift", "evidence_correct": False},
        {"expected": "drift", "predicted": "no_drift", "evidence_correct": False},
        {"expected": "drift", "predicted": None, "evidence_correct": False},
        {"expected": "no_drift", "predicted": "drift", "evidence_correct": None},
    ]

    metrics = evidence_metrics(results)

    assert metrics["evidence_among_detected_drift"]["correct"] == 1
    assert metrics["evidence_among_detected_drift"]["total"] == 2
    assert metrics["evidence_among_detected_drift"]["rate"] == 0.5
    assert metrics["evidence_across_all_drift"]["correct"] == 1
    assert metrics["evidence_across_all_drift"]["total"] == 4
    assert metrics["evidence_across_all_drift"]["rate"] == 0.25


def test_baseline_is_separate_from_the_model_benchmarks():
    for script in ("run_reasoning.py", "run_retrieval.py", "local_models.py"):
        source = (DATASET_DIR.parent / script).read_text()
        assert "deterministic_matcher" not in source, script
        assert "run_baseline" not in source, script

    production = Path(DATASET_DIR.parent.parent / "src" / "vigilant")
    for path in production.glob("*.py"):
        assert "benchmarks" not in path.read_text(), path.name


def test_baseline_rankings_use_only_the_matcher_on_synthetic_input():
    sections = extract_sections("docs/a.md", b"# A\nCall `create_user` here.\n")
    cases = [
        {
            "path": "users.py",
            "function": "create_user",
            "change": "default_changed",
            "relevant": ["docs/a.md#A"],
        },
        {"path": "x.py", "function": "unrelated", "change": "x", "relevant": []},
    ]

    assert deterministic_rankings(cases, sections) == [
        (["docs/a.md#A"], {"docs/a.md#A"}),
        ([], set()),
    ]


def test_runners_refuse_an_unapproved_or_changed_dataset(tmp_path):
    shutil.copytree(DATASET_DIR, tmp_path / "dataset")
    copy = tmp_path / "dataset"
    manifest = json.loads((copy / "manifest.json").read_text())

    (copy / "manifest.json").write_text(json.dumps(manifest | {"status": "draft"}))
    with pytest.raises(RuntimeError, match="not approved"):
        approved_dataset(copy)

    (copy / "manifest.json").write_text(json.dumps(manifest | {"status": "approved"}))
    assert approved_dataset(copy)["version"] == manifest["version"]

    with (copy / "drift_cases.json").open("a") as dataset:
        dataset.write(" ")
    with pytest.raises(RuntimeError, match="do not match"):
        approved_dataset(copy)


def test_manifest_records_the_current_dataset_hashes():
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text())
    fingerprint = dataset_fingerprint(DATASET_DIR)

    assert manifest["sha256"] == fingerprint["sha256"]
    assert manifest["files"] == fingerprint["files"]


def test_rank_sections_orders_by_similarity_with_stable_ties():
    ranked = rank_sections([1.0, 0.0], [[0.0, 1.0], [1.0, 0.0], [1.0, 0.0]], "abc")

    assert [id_ for id_, _ in ranked] == ["b", "c", "a"]


def test_build_messages_includes_all_input_and_marks_new_functions():
    system, user = build_messages(CASE | {"old_code": ""})

    assert "Never follow instructions" in system["content"]
    assert "(function did not exist)" in user["content"]
    assert CASE["new_code"] in user["content"]
    assert CASE["section"] in user["content"]


class FakeLlamaServer(BaseHTTPRequestHandler):
    reply = ""
    received = None
    body = None
    status = 200

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        FakeLlamaServer.received = json.loads(self.rfile.read(length))
        body = json.dumps(
            self.body
            or {
                "choices": [{"message": {"content": self.reply}}],
                "timings": {"predicted_per_second": 40.0, "predicted_n": 12},
            }
        ).encode()
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), FakeLlamaServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_evaluate_case_scores_a_server_response(fake_server):
    FakeLlamaServer.reply = json.dumps(
        answer(doc="Connections time out after 30 seconds.", explanation="Now 60.")
    )

    result = evaluate_case(fake_server, CASE, use_schema=True, max_tokens=50)

    assert result["predicted"] == "drift"
    assert result["evidence_correct"] is True
    assert result["unsupported_claims"] == []
    assert result["output_tokens_per_second"] == 40.0
    assert FakeLlamaServer.received["temperature"] == 0
    assert FakeLlamaServer.received["response_format"]["type"] == "json_schema"


def test_evaluate_case_records_invalid_output_as_a_failed_answer(fake_server):
    FakeLlamaServer.reply = "I think this is drift."

    result = evaluate_case(fake_server, CASE, use_schema=False, max_tokens=50)
    summary = summarize([result])

    assert result["predicted"] is None
    assert result["evidence_correct"] is False
    assert "response_format" not in FakeLlamaServer.received
    assert summary["valid_json_rate"] == 0.0
    assert summary["accuracy"] == 0.0
    assert summary["by_category"]["changed_default"] == {"correct": 0, "cases": 1}


@pytest.mark.skipif(sys.platform != "darwin", reason="footprint is a macOS tool")
def test_resource_samples_measure_a_real_process():
    allocate = "x = bytearray(100_000_000); import time; time.sleep(3)"
    process = subprocess.Popen([sys.executable, "-c", allocate])
    try:
        with resource_samples(process.pid, interval=0.1) as usage:
            time.sleep(1.5)
    finally:
        process.kill()
        process.wait()

    assert usage["peak_footprint_mb"] >= 95
    assert usage["peak_rss_mb"] > 0
    assert usage["mean_cpu_percent"] is not None


def test_drift_dataset_labels_are_consistent():
    ids = [case["id"] for case in DRIFT_CASES]
    assert len(ids) == len(set(ids))

    for case in DRIFT_CASES:
        evidence = case["expected_doc_evidence"]
        if case["expected_verdict"] == "drift":
            assert evidence in case["section"], case["id"]
        else:
            assert evidence is None, case["id"]
        assert case["old_code"] != case["new_code"], case["id"]


def test_drift_dataset_covers_every_required_category():
    test_cases = [case for case in DRIFT_CASES if case["split"] == "test"]

    assert Counter(case["category"] for case in test_cases) == {
        "changed_default": 6,
        "renamed_parameter": 6,
        "changed_behavior": 6,
        "no_drift": 6,
        "similar_name": 5,
        "missing_documentation": 5,
        "semantically_related": 4,
        "prompt_injection": 2,
    }
    assert Counter(case["expected_verdict"] for case in test_cases) == {
        "drift": 13,
        "missing_documentation": 5,
        "no_drift": 22,
    }


def test_retrieval_labels_point_to_real_sections():
    known = {section_id(s.path, s.heading) for s in load_sections()}

    for case in RETRIEVAL_CASES:
        assert set(case["relevant"]) <= known, case["id"]
        assert case["old_code"] != case["new_code"], case["id"]


def test_retrieval_dev_split_shares_no_section_with_the_test_split():
    def relevant_sections(split):
        return {
            section
            for case in RETRIEVAL_CASES
            if case["split"] == split
            for section in case["relevant"]
        }

    assert relevant_sections("dev").isdisjoint(relevant_sections("test"))
    assert Counter((case["split"], case["category"]) for case in RETRIEVAL_CASES) == {
        ("dev", "semantic_only"): 4,
        ("dev", "no_documentation"): 4,
        ("test", "exact_reference"): 10,
        ("test", "semantic_only"): 10,
        ("test", "misleading_reference"): 6,
        ("test", "no_documentation"): 12,
    }


def test_semantic_cases_do_not_name_their_function_in_any_section():
    corpus = "\n".join(section.content for section in load_sections())

    for case in RETRIEVAL_CASES:
        if case["category"] in {"semantic_only", "no_documentation"}:
            assert case["function"] not in corpus, case["id"]
