import hashlib
import json
import math
import re
from pathlib import Path

VERDICTS = {"drift", "missing_documentation", "no_drift"}
POSITIVE_VERDICTS = {"drift", "missing_documentation"}
BACKTICKED = re.compile(r"`([^`]+)`")
MIN_QUOTE_COVERAGE = 0.5
MAX_QUOTE_LENGTH_RATIO = 2


def dataset_fingerprint(dataset_dir: Path) -> dict:
    """Hash the dataset files, and all of them together, in a stable order."""
    paths = [dataset_dir / "drift_cases.json", dataset_dir / "retrieval_cases.json"]
    paths += sorted((dataset_dir / "docs").glob("*.md"))
    files = {
        path.relative_to(dataset_dir)
        .as_posix(): hashlib.sha256(path.read_bytes())
        .hexdigest()
        for path in paths
    }
    lines = "".join(f"{name} {digest}\n" for name, digest in files.items())
    return {"files": files, "sha256": hashlib.sha256(lines.encode()).hexdigest()}


def approved_dataset(dataset_dir: Path) -> dict:
    """Return the manifest, refusing a dataset that is unapproved or has changed."""
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    if manifest["status"] != "approved":
        raise RuntimeError(f"dataset is not approved: {manifest['status']}")
    if dataset_fingerprint(dataset_dir)["sha256"] != manifest["sha256"]:
        raise RuntimeError("dataset files do not match the approved manifest")
    return manifest


def wilson_interval(successes: int, total: int) -> tuple[float, float] | None:
    """Return the 95% confidence interval of a proportion."""
    if total == 0:
        return None
    z = 1.96
    rate = successes / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    )
    return (round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3))


def classification_metrics(pairs: list[tuple[str, str | None]]) -> dict:
    """Score (expected, predicted) verdicts; a failed response counts as wrong."""
    true_positive = false_positive = false_negative = correct = 0
    for expected, predicted in pairs:
        correct += expected == predicted
        expected_positive = expected in POSITIVE_VERDICTS
        predicted_positive = predicted in POSITIVE_VERDICTS
        true_positive += expected_positive and predicted_positive
        false_positive += predicted_positive and not expected_positive
        false_negative += expected_positive and not predicted_positive

    predicted_count = true_positive + false_positive
    actual_count = true_positive + false_negative
    precision = true_positive / predicted_count if predicted_count else 0.0
    recall = true_positive / actual_count if actual_count else 0.0
    total_score = precision + recall
    return {
        "cases": len(pairs),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 3),
        "precision_interval": wilson_interval(true_positive, predicted_count),
        "recall": round(recall, 3),
        "recall_interval": wilson_interval(true_positive, actual_count),
        "f1": round(2 * precision * recall / total_score, 3) if total_score else 0.0,
        "accuracy": round(correct / len(pairs), 3) if pairs else 0.0,
        "accuracy_interval": wilson_interval(correct, len(pairs)),
    }


def parse_response(text: str) -> dict | None:
    """Return the model's JSON object, or None if it is not a complete answer."""
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS:
        return None
    fields = ("doc_evidence", "code_evidence", "explanation")
    if not all(isinstance(answer.get(field), str) for field in fields):
        return None
    return answer


def evidence_is_correct(case: dict, answer: dict) -> bool | None:
    """Score the documentation quote against the labelled evidence.

    Returns None for cases with no labelled evidence. The rules are defined in
    benchmarks/README.md.
    """
    expected = case["expected_doc_evidence"]
    if expected is None:
        return None
    quote = _normalized(answer["doc_evidence"])
    expected = _normalized(expected)
    if not quote or quote not in _normalized(case["section"]):
        return False
    if quote in expected:
        return len(quote) >= MIN_QUOTE_COVERAGE * len(expected)
    return expected in quote and len(quote) <= MAX_QUOTE_LENGTH_RATIO * len(expected)


def evidence_metrics(results: list[dict]) -> dict:
    """Report evidence among detected drift and among all actual drift separately."""
    actual = [r for r in results if r["expected"] == "drift"]
    detected = [r for r in actual if r["predicted"] == "drift"]
    return {
        "evidence_among_detected_drift": _evidence_score(detected),
        "evidence_across_all_drift": _evidence_score(actual),
    }


def _evidence_score(results: list[dict]) -> dict:
    correct = sum(r["evidence_correct"] is True for r in results)
    return {
        "correct": correct,
        "total": len(results),
        "rate": _rate(correct, len(results)),
        "interval": wilson_interval(correct, len(results)),
    }


def unsupported_claims(case: dict, answer: dict) -> list[str]:
    """List quotes and backticked identifiers that are absent from the model's input.

    This only finds claims that can be checked mechanically, so a paraphrased false
    statement is not detected.
    """
    section = _normalized(case["section"])
    code = _normalized(f"{case['old_code']} {case['new_code']}")
    known_text = " ".join(
        [
            section,
            code,
            _normalized(case["change"]),
            case["function"],
            case["doc_path"],
        ]
    )

    claims = []
    doc_quote = _normalized(answer["doc_evidence"])
    if doc_quote and doc_quote not in section:
        claims.append(f"doc quote: {answer['doc_evidence']}")
    code_quote = _normalized(answer["code_evidence"])
    if code_quote and code_quote not in code:
        claims.append(f"code quote: {answer['code_evidence']}")
    for identifier in BACKTICKED.findall(answer["explanation"]):
        if _normalized(identifier).removesuffix("()") not in known_text:
            claims.append(f"identifier: {identifier}")
    return claims


def retrieval_metrics(rankings: list[tuple[list[str], set[str]]]) -> dict:
    """Score (ranked section ids, relevant section ids) for queries with answers."""
    scored = [(ranked, relevant) for ranked, relevant in rankings if relevant]
    metrics = {"queries": len(scored)}
    for k in (1, 3, 5):
        hits = sum(bool(set(ranked[:k]) & relevant) for ranked, relevant in scored)
        metrics[f"recall_at_{k}"] = round(hits / len(scored), 3) if scored else 0.0
    reciprocal_ranks = [_reciprocal_rank(ranked, rel) for ranked, rel in scored]
    metrics["mrr"] = round(sum(reciprocal_ranks) / len(scored), 3) if scored else 0.0
    return metrics


def _reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for position, section_id in enumerate(ranked, start=1):
        if section_id in relevant:
            return 1 / position
    return 0.0


def set_retrieval_metrics(rankings: list[tuple[list[str], set[str]]]) -> dict:
    """Score an unranked result set, as returned by the deterministic matcher."""
    returned = found = relevant_total = 0
    for ranked, relevant in rankings:
        returned += len(ranked)
        found += len(set(ranked) & relevant)
        relevant_total += len(relevant)
    return {
        "returned": returned,
        "precision": round(found / returned, 3) if returned else 0.0,
        "recall": round(found / relevant_total, 3) if relevant_total else 0.0,
    }


def abstention_metrics(outcomes: list[tuple[bool, bool]]) -> dict:
    """Score (returned a match, query has relevant documentation) outcomes."""
    undocumented = [returned for returned, documented in outcomes if not documented]
    documented = [returned for returned, is_doc in outcomes if is_doc]
    false_positives = sum(undocumented)
    abstained = len(undocumented) - false_positives
    missed = len(documented) - sum(documented)
    return {
        "undocumented_queries": len(undocumented),
        "false_positive_rate": _rate(false_positives, len(undocumented)),
        "false_positive_interval": wilson_interval(false_positives, len(undocumented)),
        "abstention_rate": _rate(abstained, len(undocumented)),
        "documented_queries": len(documented),
        "false_no_match_rate": _rate(missed, len(documented)),
        "false_no_match_interval": wilson_interval(missed, len(documented)),
    }


def choose_threshold(scored: list[tuple[float, bool]]) -> float:
    """Pick the similarity cut-off that best separates (top score, documented).

    Candidates are the observed scores. The best balanced accuracy wins, and the
    highest candidate wins a tie, which prefers abstaining over a false match.
    """

    def balanced_accuracy(threshold: float) -> float:
        outcomes = [(score >= threshold, documented) for score, documented in scored]
        metrics = abstention_metrics(outcomes)
        return (metrics["abstention_rate"] + 1 - metrics["false_no_match_rate"]) / 2

    candidates = sorted({score for score, _ in scored}, reverse=True)
    return max(candidates, key=balanced_accuracy)


def score_separation(scored: list[tuple[float, bool]]) -> float:
    """Return the chance that a documented query outscores an undocumented one."""
    documented = [score for score, is_documented in scored if is_documented]
    undocumented = [score for score, is_documented in scored if not is_documented]
    wins = sum(
        1.0 if left > right else 0.5 if left == right else 0.0
        for left in documented
        for right in undocumented
    )
    return round(wins / (len(documented) * len(undocumented)), 3)


def _rate(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0


def _normalized(text: str) -> str:
    return " ".join(text.split())
