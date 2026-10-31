"""Compare one reasoning request per function with one request per retrieved section.

This uses development data only. Pair mode needs a rule to combine its per-section
verdicts into one, which exists here only so the two shapes can be compared.
"""

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from benchmarks.local_models import resource_samples
from benchmarks.metrics import wilson_interval
from vigilant.ast_diff import ChangedFunction, ChangeType, FunctionChange
from vigilant.drift import ScanSettings
from vigilant.local_models import llama_server
from vigilant.markdown_docs import Section
from vigilant.reasoning import (
    SYSTEM_PROMPT,
    VERDICTS,
    Assessment,
    _comparable,
    assess_function,
)

DEV_CASES = Path(__file__).parent / "dataset" / "dev_cases.json"
COMBINE_ORDER = ("drift", "missing_documentation", "no_drift", "needs_review")


def load_case(case: dict) -> tuple[ChangedFunction, list[Section]]:
    function = ChangedFunction(
        path=case["path"],
        function=case["function"],
        changes=(
            FunctionChange(
                case["path"],
                case["function"],
                ChangeType(case["change_type"]),
                case["change"],
                None,
                None,
                1,
                1,
            ),
        ),
        old_code=case["old_code"] or None,
        new_code=case["new_code"] or None,
        old_line=1,
        new_line=1,
    )
    sections = [
        Section(
            path=s["path"],
            heading=s["heading"],
            hierarchy=(s["heading"],),
            start_line=1,
            end_line=s["content"].count("\n") + 1,
            content=s["content"],
            references=(),
        )
        for s in case["sections"]
    ]
    return function, sections


def combine(assessments: list[Assessment]) -> str:
    verdicts = [a.verdict for a in assessments]
    return next(v for v in COMBINE_ORDER if v in verdicts)


def evidence_is_correct(
    case: dict, assessments: list[Assessment], verdict: str
) -> bool:
    """Every labelled quote must appear in returned evidence from its own section."""
    returned = [
        (e.path, _comparable(e.text))
        for a in assessments
        if a.verdict == verdict
        for e in a.evidence
    ]
    for expected in case["expected_evidence"]:
        path = case["sections"][expected["section"] - 1]["path"]
        quote = _comparable(expected["quote"])
        if not any(p == path and quote in text for p, text in returned):
            return False
    return True


def evaluate_case(url: str, case: dict, mode: str, max_tokens: int) -> dict:
    function, sections = load_case(case)
    started = time.perf_counter()
    if mode == "function":
        assessments = [assess_function(url, function, sections, max_tokens)]
    else:
        assessments = [
            assess_function(url, function, [section], max_tokens)
            for section in sections
        ]
    latency = time.perf_counter() - started
    verdict = combine(assessments)
    return {
        "id": case["id"],
        "category": case["category"],
        "expected": case["expected_verdict"],
        "predicted": verdict,
        "calls": len(assessments),
        "latency_seconds": round(latency, 3),
        "prompt_tokens": [a.prompt_tokens for a in assessments],
        "evidence_correct": (
            evidence_is_correct(case, assessments, verdict)
            if case["expected_evidence"]
            else None
        ),
        "downgraded": [
            a.review_reason for a in assessments if a.model_verdict != a.verdict
        ],
        "responses": [a.raw_response for a in assessments],
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)
    correct = sum(r["predicted"] == r["expected"] for r in results)
    scored = [
        r["evidence_correct"] for r in results if r["evidence_correct"] is not None
    ]
    false_drift = [
        r for r in results if r["predicted"] == "drift" and r["expected"] != "drift"
    ]
    latencies = sorted(r["latency_seconds"] for r in results)
    prompt_tokens = sorted(t for r in results for t in r["prompt_tokens"] if t)
    return {
        "cases": total,
        "accuracy": round(correct / total, 3),
        "accuracy_interval": wilson_interval(correct, total),
        "confusion": {
            expected: dict(
                Counter(r["predicted"] for r in results if r["expected"] == expected)
            )
            for expected in VERDICTS
        },
        "false_drift": len(false_drift),
        "needs_review_predicted": sum(
            r["predicted"] == "needs_review" for r in results
        ),
        "evidence_correct": sum(scored),
        "evidence_scored": len(scored),
        "downgraded_cases": sum(bool(r["downgraded"]) for r in results),
        "calls": sum(r["calls"] for r in results),
        "total_seconds": round(sum(latencies), 1),
        "median_seconds_per_function": latencies[total // 2],
        "max_seconds_per_function": latencies[-1],
        "median_prompt_tokens_per_call": prompt_tokens[len(prompt_tokens) // 2],
        "max_prompt_tokens_per_call": prompt_tokens[-1],
        "by_category": {
            category: f"{sum(r['predicted'] == r['expected'] for r in results if r['category'] == category)}"
            f"/{sum(r['category'] == category for r in results)}"
            for category in sorted({r["category"] for r in results})
        },
    }


def main() -> None:
    settings = ScanSettings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["function", "pair"], required=True)
    parser.add_argument("--model", type=Path, default=settings.coder_model)
    parser.add_argument("--llama-server", default=settings.llama_server)
    parser.add_argument("--ctx-size", type=int, default=settings.ctx_size)
    parser.add_argument("--max-tokens", type=int, default=settings.max_tokens)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset = DEV_CASES.read_bytes()
    cases = json.loads(dataset)
    server_args = [
        "--n-gpu-layers",
        "99",
        "--threads",
        "4",
        "--ctx-size",
        str(args.ctx_size),
        "--parallel",
        "1",
    ]
    with llama_server(args.model, server_args, args.llama_server) as server:
        with resource_samples(server["pid"]) as usage:
            results = [
                evaluate_case(server["url"], case, args.mode, args.max_tokens)
                for case in cases
            ]

    report = {
        "mode": args.mode,
        "data": "development cases, not a held-out evaluation",
        "dataset_sha256": hashlib.sha256(dataset).hexdigest(),
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "model": args.model.name,
        "ctx_size": args.ctx_size,
        "load_seconds": server["load_seconds"],
        "usage": usage,
        "summary": summarize(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
