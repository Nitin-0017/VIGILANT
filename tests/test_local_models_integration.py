"""Tests that run the real local models. Enable with VIGILANT_RUN_MODEL_TESTS=1.

These check that the real models run through the pipeline and return supported output.
They are not an accuracy evaluation.
"""

import json
import os
import subprocess
import sys

import pytest

from vigilant.drift import ScanSettings
from vigilant.local_models import ContextExceededError, llama_embeddings, llama_server
from vigilant.local_models import post_json
from vigilant.reasoning import VERDICTS
from vigilant.retrieval import cosine

pytestmark = pytest.mark.skipif(
    os.environ.get("VIGILANT_RUN_MODEL_TESTS") != "1",
    reason="set VIGILANT_RUN_MODEL_TESTS=1 to run the real local models",
)
SETTINGS = ScanSettings()
EMBEDDING_ARGS = ["--embedding", "--pooling", "last", "--ctx-size", "8192"] + [
    "--batch-size",
    "8192",
    "--ubatch-size",
    "8192",
    "--parallel",
    "1",
    "--n-gpu-layers",
    "99",
]


def scan(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "vigilant", "scan", *args, "--format", "json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_real_embedding_model_ranks_the_related_text_higher():
    with llama_server(SETTINGS.embedding_model, EMBEDDING_ARGS) as server:
        query, related, unrelated = llama_embeddings(
            server["url"],
            [
                "Instruct: Find the passage\nQuery:How long until a database connection times out?",
                "Connections to the database time out after 30 seconds.",
                "The cafeteria serves soup on Fridays.",
            ],
        )

    assert len(query) == 1024
    assert cosine(query, related) > cosine(query, unrelated)


def test_real_reasoning_server_reports_an_oversized_input():
    args = ["--n-gpu-layers", "99", "--ctx-size", "64", "--parallel", "1"]
    with llama_server(SETTINGS.coder_model, args) as server:
        with pytest.raises(ContextExceededError):
            post_json(
                f"{server['url']}/v1/chat/completions",
                {"messages": [{"role": "user", "content": "word " * 500}]},
            )


def test_real_scan_returns_a_supported_finding(git_repo):
    base = git_repo.commit(
        {
            "db.py": "def connect(host, timeout=30):\n    return open_socket(host, timeout)\n",
            "docs/db.md": (
                "# Database\n\n## Connecting\n\n"
                "`connect` opens a connection. It times out after 30 seconds by default.\n\n"
                "## Backups\n\nBackups run every night.\n"
            ),
        }
    )
    target = git_repo.commit(
        {
            "db.py": "def connect(host, timeout=60):\n    return open_socket(host, timeout)\n"
        }
    )

    result = scan(git_repo.path, base, target)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    finding = report["findings"][0]
    assert finding["verdict"] in VERDICTS
    assert finding["retrieved"][0]["heading"] == "Connecting"
    assert finding["raw_response"]
    if finding["verdict"] != "needs_review":
        assert finding["evidence"]
        for evidence in finding["evidence"]:
            source = (git_repo.path / evidence["path"]).read_text().split("\n")
            assert evidence["text"].split("\n")[0] == source[evidence["line"] - 1]


def test_real_scan_reports_a_missing_model_as_an_error(git_repo, tmp_path):
    base = git_repo.commit({"a.py": "def f(a):\n    pass\n", "a.md": "# A\n\nText.\n"})
    target = git_repo.commit({"a.py": "def f(b):\n    pass\n"})

    result = scan(
        git_repo.path, base, target, "--coder-model", str(tmp_path / "absent.gguf")
    )

    assert result.returncode == 1
    assert "model file not found" in result.stderr
    assert result.stdout == ""
