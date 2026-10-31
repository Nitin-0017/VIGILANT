from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vigilant.ast_diff import ChangedFunction
from vigilant.local_models import (
    DEFAULT_MODEL_DIR,
    ModelExecutionError,
    llama_embeddings,
    llama_server,
)
from vigilant.markdown_docs import Section
from vigilant.reasoning import Assessment, assess_function
from vigilant.retrieval import RetrievedSection, retrieve_sections


@dataclass(frozen=True)
class ScanSettings:
    coder_model: Path = DEFAULT_MODEL_DIR / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    embedding_model: Path = DEFAULT_MODEL_DIR / "Qwen3-Embedding-0.6B-Q8_0.gguf"
    llama_server: str = "llama-server"
    top_k: int = 3
    ctx_size: int = 8192
    embedding_ctx_size: int = 8192
    max_tokens: int = 600
    gpu_layers: int = 99
    threads: int = 4


@dataclass(frozen=True)
class Finding:
    function: ChangedFunction
    assessment: Assessment
    retrieved: tuple[RetrievedSection, ...] = field(default=())


def detect_drift(
    functions: list[ChangedFunction],
    sections: list[Section],
    settings: ScanSettings,
    progress: Callable[[str], None] = lambda message: None,
) -> list[Finding]:
    """Run retrieval and reasoning; any model failure raises ModelExecutionError."""
    if not functions:
        return []
    if not sections:
        return [_no_documentation_finding(function) for function in functions]

    for model in (settings.embedding_model, settings.coder_model):
        if not model.is_file():
            raise ModelExecutionError(f"model file not found: {model}")

    # One model at a time keeps peak memory lower on machines with little of it.
    progress(f"Embedding {len(sections)} documentation sections")
    retrieved = _retrieve(functions, sections, settings)

    server_args = [
        "--n-gpu-layers",
        str(settings.gpu_layers),
        "--threads",
        str(settings.threads),
        "--ctx-size",
        str(settings.ctx_size),
        "--parallel",
        "1",
    ]
    findings = []
    with llama_server(
        settings.coder_model, server_args, settings.llama_server
    ) as server:
        for number, (function, results) in enumerate(
            zip(functions, retrieved), start=1
        ):
            progress(f"Assessing {function.function} ({number}/{len(functions)})")
            assessment = assess_function(
                server["url"],
                function,
                [result.section for result in results],
                settings.max_tokens,
            )
            findings.append(Finding(function, assessment, tuple(results)))
    return findings


def _retrieve(
    functions: list[ChangedFunction],
    sections: list[Section],
    settings: ScanSettings,
) -> list[list[RetrievedSection]]:
    server_args = [
        "--embedding",
        "--pooling",
        "last",
        "--ctx-size",
        str(settings.embedding_ctx_size),
        "--batch-size",
        str(settings.embedding_ctx_size),
        "--ubatch-size",
        str(settings.embedding_ctx_size),
        "--parallel",
        "1",
        "--n-gpu-layers",
        str(settings.gpu_layers),
    ]
    with llama_server(
        settings.embedding_model, server_args, settings.llama_server
    ) as server:

        def embed(texts: list[str]) -> list[list[float]]:
            return llama_embeddings(server["url"], texts)

        return retrieve_sections(embed, functions, sections, settings.top_k)


def _no_documentation_finding(function: ChangedFunction) -> Finding:
    return Finding(
        function,
        Assessment(
            verdict="needs_review",
            model_verdict=None,
            evidence=(),
            code_evidence="",
            explanation="",
            review_reason="the repository has no documentation sections to assess",
            raw_response="",
            prompt_tokens=None,
            latency_seconds=0.0,
        ),
    )
