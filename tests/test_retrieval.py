import pytest

from vigilant.ast_diff import ChangedFunction, ChangeType, FunctionChange
from vigilant.local_models import ModelExecutionError
from vigilant.markdown_docs import extract_sections
from vigilant.retrieval import (
    BATCH_SIZE,
    INSTRUCTION,
    cosine,
    query_text,
    retrieve_sections,
)

FUNCTION = ChangedFunction(
    path="app/db.py",
    function="connect",
    changes=(
        FunctionChange(
            "app/db.py",
            "connect",
            ChangeType.DEFAULT_CHANGED,
            "timeout: 30 -> 60",
            "def connect(timeout=30)",
            "def connect(timeout=60)",
            1,
            1,
        ),
    ),
    old_code="def connect(timeout=30): pass",
    new_code="def connect(timeout=60): pass",
    old_line=1,
    new_line=1,
)

SECTIONS = extract_sections("a.md", b"# First\ntext\n# Second\ntext\n# Third\ntext\n")


def test_query_has_instruction_change_and_both_code_versions():
    query = query_text(FUNCTION)

    assert query.startswith(f"Instruct: {INSTRUCTION}\nQuery:Function connect")
    assert "app/db.py" in query
    assert "timeout: 30 -> 60" in query
    assert FUNCTION.old_code in query and FUNCTION.new_code in query


def test_query_handles_added_and_removed_functions():
    added = ChangedFunction("a.py", "f", (), None, "def f(): pass", None, 1)

    assert "Old code:\n\nNew code:\ndef f(): pass" in query_text(added)


def test_retrieve_returns_top_k_in_similarity_order():
    vectors = {
        SECTIONS[0].content: [0.0, 1.0],
        SECTIONS[1].content: [1.0, 0.0],
        SECTIONS[2].content: [1.0, 1.0],
    }

    def embed(texts):
        return [vectors.get(text, [1.0, 0.0]) for text in texts]

    retrieved = retrieve_sections(embed, [FUNCTION], SECTIONS, top_k=2)

    assert [r.section.heading for r in retrieved[0]] == ["Second", "Third"]
    assert retrieved[0][0].score == pytest.approx(1.0)


def test_retrieve_applies_no_score_cut_off():
    def embed(texts):
        return [
            [1.0, 0.0] if text.startswith("Instruct") else [-1.0, 0.001]
            for text in texts
        ]

    retrieved = retrieve_sections(embed, [FUNCTION], SECTIONS, top_k=3)

    assert len(retrieved[0]) == 3
    assert all(result.score < 0 for result in retrieved[0])


def test_ties_are_broken_by_path_and_line():
    retrieved = retrieve_sections(
        lambda texts: [[1.0] for _ in texts], [FUNCTION], SECTIONS, top_k=3
    )

    assert [r.section.start_line for r in retrieved[0]] == [1, 3, 5]


def test_embeddings_are_requested_in_batches():
    sections = extract_sections(
        "a.md", "".join(f"# H{i}\nx\n" for i in range(BATCH_SIZE + 1)).encode()
    )
    sizes = []

    def embed(texts):
        sizes.append(len(texts))
        return [[1.0] for _ in texts]

    retrieve_sections(embed, [FUNCTION], sections, top_k=1)

    assert sizes == [BATCH_SIZE, 1, 1]


def test_embedding_failure_propagates():
    def embed(texts):
        raise ModelExecutionError("embedding failed")

    with pytest.raises(ModelExecutionError, match="embedding failed"):
        retrieve_sections(embed, [FUNCTION], SECTIONS, top_k=1)


def test_cosine_handles_zero_vectors():
    assert cosine([0.0], [1.0]) == 0.0
    assert cosine([2.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
