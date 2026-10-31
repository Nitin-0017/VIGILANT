"""Evaluate Qwen3-Embedding-0.6B retrieval in two local runtimes."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from benchmarks.local_models import resource_samples
from benchmarks.metrics import (
    abstention_metrics,
    approved_dataset,
    choose_threshold,
    retrieval_metrics,
    score_separation,
)
from vigilant.local_models import (
    DEFAULT_MODEL_DIR,
    ModelExecutionError,
    llama_embeddings,
    llama_server,
)
from vigilant.markdown_docs import Section, extract_sections
from vigilant.retrieval import cosine

BENCHMARK_DIR = Path(__file__).parent
DATASET_DIR = BENCHMARK_DIR / "dataset"
DOCS_DIR = DATASET_DIR / "docs"
CASES = DATASET_DIR / "retrieval_cases.json"
DEFAULT_GGUF = DEFAULT_MODEL_DIR / "Qwen3-Embedding-0.6B-Q8_0.gguf"
DEFAULT_WEIGHTS = DEFAULT_MODEL_DIR / "qwen3-embedding-0.6b"
INSTRUCTION = (
    "Given a changed Python function, retrieve documentation sections that "
    "describe its behaviour"
)
QUERY_MODES = ("change_context", "name_only")
BATCH_SIZE = 8
MAX_SEQUENCE_TOKENS = 8192


def load_sections() -> list[Section]:
    sections = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        sections += extract_sections(f"docs/{path.name}", path.read_bytes())
    return sections


def section_id(path: str, heading: str | None) -> str:
    return f"{path}#{heading}"


def query_text(case: dict, mode: str) -> str:
    if mode == "name_only":
        query = f"Function {case['function']}"
    else:
        query = (
            f"Function {case['function']} in {case['path']}\n"
            f"Change: {case['change']}\n"
            f"Old code:\n{case['old_code']}\n"
            f"New code:\n{case['new_code']}"
        )
    return f"Instruct: {INSTRUCTION}\nQuery:{query}"


def embedding_inputs(cases: list[dict], sections: list[Section]) -> dict:
    """Build the exact texts that every embedding runtime receives."""
    return {
        "sections": [section.content for section in sections],
        **{mode: [query_text(case, mode) for case in cases] for mode in QUERY_MODES},
    }


def rank_sections(
    query: list[float], section_vectors: list[list[float]], ids: list[str]
) -> list[tuple[str, float]]:
    scored = [(id_, cosine(query, vector)) for id_, vector in zip(ids, section_vectors)]
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def embedding_report(
    embed, cases: list[dict], sections: list[Section]
) -> tuple[dict, list[list[float]]]:
    """Evaluate one runtime; `embed` must turn one batch of texts into vectors."""
    inputs = embedding_inputs(cases, sections)
    ids = [section_id(s.path, s.heading) for s in sections]

    section_vectors, sections_seconds = _embed_all(embed, inputs["sections"])
    report = {
        "inputs_sha256": hashlib.sha256(
            json.dumps(inputs, sort_keys=True).encode()
        ).hexdigest(),
        "batch_size": BATCH_SIZE,
        "max_sequence_tokens": MAX_SEQUENCE_TOKENS,
        "dimensions": len(section_vectors[0]),
        "sections_seconds": round(sections_seconds, 3),
        "seconds_per_section": round(sections_seconds / len(sections), 4),
    }
    for mode in QUERY_MODES:
        query_vectors, query_seconds = _embed_all(embed, inputs[mode])
        report[mode] = _mode_report(cases, query_vectors, section_vectors, ids) | {
            "seconds_per_query": round(query_seconds / len(cases), 4)
        }
    return report, section_vectors


def _embed_all(embed, texts: list[str]) -> tuple[list[list[float]], float]:
    started = time.perf_counter()
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors += embed(texts[start : start + BATCH_SIZE])
    return vectors, time.perf_counter() - started


def _mode_report(
    cases: list[dict],
    query_vectors: list[list[float]],
    section_vectors: list[list[float]],
    ids: list[str],
) -> dict:
    scored_queries = []
    for case, vector in zip(cases, query_vectors):
        ranked = rank_sections(vector, section_vectors, ids)
        scored_queries.append(
            {
                "id": case["id"],
                "split": case["split"],
                "category": case["category"],
                "relevant": case["relevant"],
                "top_score": ranked[0][1],
                "ranked": [id_ for id_, _ in ranked],
                "top_5": [[id_, round(score, 4)] for id_, score in ranked[:5]],
            }
        )

    # The cut-off comes from the dev split only and is then fixed for the test split.
    threshold = choose_threshold(
        [
            (q["top_score"], bool(q["relevant"]))
            for q in scored_queries
            if q["split"] == "dev"
        ]
    )
    test_queries = [q for q in scored_queries if q["split"] == "test"]
    rankings = [(q["ranked"], set(q["relevant"])) for q in test_queries]
    scores = [(q["top_score"], bool(q["relevant"])) for q in test_queries]
    return {
        "metrics": retrieval_metrics(rankings),
        "by_category": metrics_by_category(test_queries, rankings),
        "threshold_from_dev": round(threshold, 4),
        "abstention": abstention_metrics(
            [(score >= threshold, documented) for score, documented in scores]
        ),
        "score_separation": score_separation(scores),
        "queries": [
            {
                key: round(value, 4) if key == "top_score" else value
                for key, value in q.items()
                if key != "ranked"
            }
            for q in test_queries
        ],
    }


def metrics_by_category(cases: list[dict], rankings: list) -> dict:
    categories = sorted({case["category"] for case in cases if case["relevant"]})
    return {
        category: retrieval_metrics(
            [
                ranking
                for case, ranking in zip(cases, rankings)
                if case["category"] == category
            ]
        )
        for category in categories
    }


def run_sentence_transformers(args, cases, sections) -> tuple[dict, list[list[float]]]:
    # No Hugging Face network access: the weights must already be on disk.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    if not args.weights.is_dir():
        raise ModelExecutionError(f"model directory not found: {args.weights}")

    started = time.perf_counter()
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            str(args.weights), device=args.device, trust_remote_code=False
        )
    except Exception as error:
        raise ModelExecutionError(f"could not load model: {error}") from error
    model.max_seq_length = MAX_SEQUENCE_TOKENS
    load_seconds = time.perf_counter() - started

    def embed(texts: list[str]) -> list[list[float]]:
        try:
            return model.encode(texts, batch_size=len(texts)).tolist()
        except Exception as error:
            raise ModelExecutionError(f"embedding failed: {error}") from error

    with resource_samples(os.getpid()) as usage:
        report, vectors = embedding_report(embed, cases, sections)
    return (
        {"device": args.device, "load_seconds": round(load_seconds, 3), "usage": usage}
        | report,
        vectors,
    )


def run_llama(args, cases, sections) -> tuple[dict, list[list[float]]]:
    server_args = [
        "--embedding",
        "--pooling",
        "last",
        "--ubatch-size",
        str(MAX_SEQUENCE_TOKENS),
        "--batch-size",
        str(MAX_SEQUENCE_TOKENS),
        "--ctx-size",
        str(MAX_SEQUENCE_TOKENS * BATCH_SIZE),
        "--parallel",
        str(BATCH_SIZE),
        "--n-gpu-layers",
        str(args.gpu_layers),
    ]
    with llama_server(args.gguf, server_args, args.llama_server) as server:

        def embed(texts: list[str]) -> list[list[float]]:
            return llama_embeddings(server["url"], texts)

        with resource_samples(server["pid"]) as usage:
            report, vectors = embedding_report(embed, cases, sections)
    return (
        {
            "model_bytes": args.gguf.stat().st_size,
            "gpu_layers": args.gpu_layers,
            "load_seconds": server["load_seconds"],
            "usage": usage,
        }
        | report,
        vectors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        choices=["sentence-transformers", "llama"],
        action="append",
        required=True,
    )
    parser.add_argument("--gguf", type=Path, default=DEFAULT_GGUF)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--llama-server", default="llama-server")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--gpu-layers", type=int, default=99)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = approved_dataset(DATASET_DIR)
    cases = json.loads(CASES.read_text())
    sections = load_sections()
    report = {
        "dataset_version": manifest["version"],
        "dataset_sha256": manifest["sha256"],
        "sections": len(sections),
    }

    vectors = {}
    for runtime in args.runtime:
        runner = (
            run_sentence_transformers
            if runtime == "sentence-transformers"
            else run_llama
        )
        report[runtime], vectors[runtime] = runner(args, cases, sections)

    if len(vectors) == 2:
        first, second = vectors.values()
        similarities = [cosine(a, b) for a, b in zip(first, second)]
        report["runtime_section_vector_cosine"] = {
            "mean": round(sum(similarities) / len(similarities), 4),
            "min": round(min(similarities), 4),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
