"""Score the deterministic matcher as a benchmark-only baseline.

This baseline shares nothing with the model benchmarks except dataset files and metric
definitions. Its results are not AI results and never feed either model.
"""

import argparse
import json
from pathlib import Path

from benchmarks.metrics import (
    abstention_metrics,
    approved_dataset,
    retrieval_metrics,
    set_retrieval_metrics,
)
from benchmarks.run_retrieval import (
    CASES,
    DATASET_DIR,
    load_sections,
    metrics_by_category,
    section_id,
)
from vigilant.ast_diff import ChangeType, FunctionChange
from vigilant.doc_matching import match_documentation
from vigilant.markdown_docs import Section


def deterministic_rankings(
    cases: list[dict], sections: list[Section]
) -> list[tuple[list[str], set[str]]]:
    rankings = []
    for case in cases:
        change = FunctionChange(
            path=case["path"],
            function=case["function"],
            change_type=ChangeType.SIGNATURE_CHANGED,
            detail=case["change"],
            old_signature=None,
            new_signature=None,
            old_line=None,
            new_line=None,
        )
        matches = match_documentation([change], sections)
        ranked = list(dict.fromkeys(section_id(m.doc_path, m.heading) for m in matches))
        rankings.append((ranked, set(case["relevant"])))
    return rankings


def baseline_report(cases: list[dict], sections: list[Section]) -> dict:
    test_cases = [case for case in cases if case["split"] == "test"]
    rankings = deterministic_rankings(test_cases, sections)
    return {
        "system": "deterministic matcher (benchmark baseline, not an AI result)",
        "metrics": retrieval_metrics(rankings),
        "unranked_set": set_retrieval_metrics(rankings),
        "abstention": abstention_metrics(
            [(bool(ranked), bool(relevant)) for ranked, relevant in rankings]
        ),
        "by_category": metrics_by_category(test_cases, rankings),
        "queries": [
            {"id": case["id"], "category": case["category"], "ranked": ranked}
            for case, (ranked, _) in zip(test_cases, rankings)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = approved_dataset(DATASET_DIR)
    report = {
        "dataset_version": manifest["version"],
        "dataset_sha256": manifest["sha256"],
    } | baseline_report(json.loads(CASES.read_text()), load_sections())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
