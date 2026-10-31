import math
from collections.abc import Callable
from dataclasses import dataclass

from vigilant.ast_diff import ChangedFunction
from vigilant.markdown_docs import Section

INSTRUCTION = (
    "Given a changed Python function, retrieve documentation sections that "
    "describe its behaviour"
)
BATCH_SIZE = 8

Embed = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class RetrievedSection:
    section: Section
    score: float


def query_text(function: ChangedFunction) -> str:
    details = "; ".join(change.detail for change in function.changes)
    return (
        f"Instruct: {INSTRUCTION}\n"
        f"Query:Function {function.function} in {function.path}\n"
        f"Change: {details}\n"
        f"Old code:\n{function.old_code or ''}\n"
        f"New code:\n{function.new_code or ''}"
    )


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def retrieve_sections(
    embed: Embed,
    functions: list[ChangedFunction],
    sections: list[Section],
    top_k: int,
) -> list[list[RetrievedSection]]:
    """Return the top_k most similar sections per function, with no score cut-off."""
    section_vectors = _embed_in_batches(embed, [s.content for s in sections])
    query_vectors = _embed_in_batches(embed, [query_text(f) for f in functions])

    retrieved = []
    for query in query_vectors:
        scored = [
            RetrievedSection(section, cosine(query, vector))
            for section, vector in zip(sections, section_vectors)
        ]
        scored.sort(key=lambda r: (-r.score, r.section.path, r.section.start_line))
        retrieved.append(scored[:top_k])
    return retrieved


def _embed_in_batches(embed: Embed, texts: list[str]) -> list[list[float]]:
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors += embed(texts[start : start + BATCH_SIZE])
    return vectors
