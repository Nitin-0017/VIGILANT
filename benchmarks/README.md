# Local model benchmark

This benchmark evaluates open-weight models for VIGILANT's documentation drift detection.
All inference runs on the local machine. It uses no hosted APIs or API keys, and the
network is needed only to download dependencies and weights.

The benchmark is separate from `src/vigilant` and regular CI, which download no models.
No model is integrated into VIGILANT yet. Results and the model decision are in
[`results/REPORT.md`](results/REPORT.md).

| Role | Model | Status |
| --- | --- | --- |
| Reasoning | Qwen2.5-Coder-7B-Instruct | Selected candidate, not production-ready |
| Reasoning | Qwen3-8B | Benchmarked alternative, not selected |
| Retrieval | Qwen3-Embedding-0.6B | Selected |

## Setup

This setup was run on macOS with Apple silicon. Model files stay outside the repository.

```bash
brew install llama.cpp
python3 -m venv ~/.cache/vigilant/venv
~/.cache/vigilant/venv/bin/pip install -e ".[dev,benchmark]"
mkdir -p ~/.cache/vigilant/models
```

Download these files into `~/.cache/vigilant/models` from the pinned revisions, then check
them with `shasum -a 256`:

| File | Repository and revision | SHA-256 |
| --- | --- | --- |
| `qwen2.5-coder-7b-instruct-q4_k_m.gguf` | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` at `13fb94bfda8c8cf22497dc57b78f391a9acb426a` | `509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c` |
| `Qwen3-8B-Q4_K_M.gguf` | `Qwen/Qwen3-8B-GGUF` at `7c41481f57cb95916b40956ab2f0b139b296d974` | `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785` |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | `Qwen/Qwen3-Embedding-0.6B-GGUF` at `370f27d7550e0def9b39c1f16d3fbaa13aa67728` | `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439` |
| `qwen3-embedding-0.6b/` (whole repository) | `Qwen/Qwen3-Embedding-0.6B` at `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | `model.safetensors`: `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd` |

Run the benchmarks from the repository root. Each needs an approved dataset, and each
command writes to the given output file:

```bash
~/.cache/vigilant/venv/bin/python -m benchmarks.run_reasoning --split test --output benchmarks/results/reasoning.json
```

```bash
~/.cache/vigilant/venv/bin/python -m benchmarks.run_reasoning --model ~/.cache/vigilant/models/Qwen3-8B-Q4_K_M.gguf --split test --reasoning off --output benchmarks/results/reasoning-qwen3-8b.json
```

```bash
~/.cache/vigilant/venv/bin/python -m benchmarks.run_retrieval --runtime sentence-transformers --output benchmarks/results/retrieval-sentence-transformers.json
```

```bash
~/.cache/vigilant/venv/bin/python -m benchmarks.run_retrieval --runtime llama --output benchmarks/results/retrieval-llama.json
```

```bash
~/.cache/vigilant/venv/bin/python -m benchmarks.run_baseline --output benchmarks/results/baseline-deterministic.json
```

`--model`, `--weights`, `--gguf`, `--llama-server`, `--gpu-layers`, `--threads`,
`--ctx-size`, `--max-tokens` and `--device` configure paths and runtime settings where
each script supports them. Run `--help` for details.

| Script | Reports | Source of its results |
| --- | --- | --- |
| `run_reasoning.py` | Qwen2.5-Coder-7B-Instruct and Qwen3-8B drift classification | Model predictions |
| `run_retrieval.py` | Qwen3-Embedding-0.6B retrieval | Model embeddings |
| `run_baseline.py` | Deterministic matcher, in its own output file | Not AI; comparison only |

The dataset's detected changes and documentation sections stand in for deterministic
preprocessing (Git diff, AST and Markdown extraction). The baseline never feeds a model,
and no model script imports it.

`run_request_shape.py` compares reasoning request shapes on development data in
`dataset/dev_cases.json`. That file is not part of the approved held-out dataset. The
matcher lives in `deterministic_matcher.py`, outside the production package.

## Model execution failures

A missing model, failed load, failed request or malformed server response raises
`ModelExecutionError`, stops the run and exits with an error. Nothing is substituted.
A response that arrives but is not a valid answer records no verdict and counts as
incorrect. The benchmark has no fallback path.

## Dataset provenance

The dataset and initial labels are synthetic. They were drafted by Claude Code and then
reviewed by the project owner. `dataset/manifest.json` records the version, approval
status and SHA-256 hashes. All three benchmark scripts refuse to run unless the status is
`approved` and the files match those hashes.

Reasoning data has 6 development cases and 40 held-out test cases. Retrieval data has 8
development queries and 38 held-out test queries. Prompts may be tuned only on the
reasoning development cases. Test data is never used to tune prompts or thresholds. The
results record the prompt hash, and any later prompt change means a disclosed new run.

## Reasoning labels

A case pairs one code change with one documentation section. Apply the rules in order
and stop at the first that fits.

1. **`no_drift`, out of scope:** the section is about a different function, tool or
   feature, even if it shares a name, topic or number.
2. **`drift`:** at least one statement in the section is false for the new code.
3. **`missing_documentation`:** every statement remains true, but the section is silent
   about one of these:
   - a new parameter, required or optional
   - a new error the function raises
   - a new operation in a module whose operations the section lists
4. **`no_drift`, otherwise:** this includes a changed value that the section does not
   state, an internal change, and a change that the section already describes.

Labels rely only on what the input shows, not on assumed side effects. Severity does
not change a label: a breaking omission that makes no statement false is still
`missing_documentation`.

### Rulings on reviewed cases

No label changed during review.

| Case | Label | Reasoning |
| --- | --- | --- |
| ms-1 | `missing_documentation` | "place an order for a customer" stays true. The section states no parameter list, so nothing is false, and rule 3 covers the new required parameter. |
| ms-3 | `missing_documentation` | The section lists what the orders module does. "create, cancel and ship" stays true and does not claim to be complete, so rule 3 covers the new operation. |
| ms-5 | `missing_documentation` | "saves the data and returns its URL" still describes the normal path and states nothing about errors. Rule 3 covers the new error. |
| cd-5 | `no_drift` | The section lists valid formats and states no default. Both formats remain valid, and rule 4 covers a changed value the section does not state. |
| bh-5 | `no_drift` | The function still returns the profile as a dictionary. The input shows no staleness rule, so rule 4 treats the cache as an internal change. |
| pi-2 | `no_drift` | `/healthz` is still correct and the section states no timeout, so rule 4 applies. The embedded instruction is data and has no effect on the label. |

## Reasoning metrics

`drift` and `missing_documentation` count as positive. A response that is invalid or
incomplete counts as a wrong verdict.

| Metric | Definition |
| --- | --- |
| Precision | True positives / predicted positives |
| Recall | True positives / actual positives |
| F1 | 2 x precision x recall / (precision + recall) |
| Accuracy | Exact three-way verdict matches / all cases |
| Intervals | 95% Wilson interval for precision, recall and accuracy |

Telling `drift` and `missing_documentation` apart affects accuracy but not precision,
recall or F1.

**Evidence correctness** is reported as two separate metrics, never combined:

| Metric | Numerator | Denominator |
| --- | --- | --- |
| Among correctly detected drift | Correct quotes in cases labelled `drift` and predicted `drift` | Cases labelled `drift` and predicted `drift` |
| Across all actual drift | Correct quotes in cases labelled `drift`, whatever the verdict | All 13 test cases labelled `drift` |

Only these cases have labelled evidence. After collapsing whitespace, with case and
Markdown kept exact, a quote is correct when all of these hold:

1. It is not empty.
2. It appears exactly in the section.
3. It overlaps the labelled evidence in one of two ways:
   - It sits inside the evidence and has at least 50% of its characters.
   - It contains the evidence and is at most twice its length.

An invalid response counts as incorrect. A valid answer must have a known verdict and text
in all three other fields; anything else is invalid. The valid JSON rate is valid answers
/ all cases, with a Wilson interval.

**Unsupported claim rate** is cases with at least one unsupported claim / all test cases.
An invalid response has no claims to check and is reported through the valid JSON rate
instead. A claim is unsupported when, after collapsing whitespace, one of these holds:

| Claim | Must appear in |
| --- | --- |
| Non-empty `doc_evidence` | The section |
| Non-empty `code_evidence` | The old or new code |
| Each backticked token in `explanation`, ignoring a trailing `()` | The section, code, detected change, function name or documentation path |

This automatic check cannot detect a false statement in plain prose. All explanations
are therefore also read by hand, and any false statements are reported as a separate
manual count that is never merged into the automatic rate.

## Retrieval methodology

Sentence Transformers uses the full-precision weights and llama.cpp uses the Q8 GGUF, so
this is a **combined runtime and precision comparison**, not a pure runtime comparison.
Any difference may come from either source.

All embedding runtimes receive the same inputs and settings from one shared code path:

- the same section text, query text and instruction prefix
- the same batches of 8 and a limit of 8192 tokens
- last-token pooling
- cosine similarity computed in Python, with ties broken by section id
- no reranking, vector database or runtime-specific prompt

Each result records a hash of its input text, which must match across runtimes. That hash
covers text before tokenization, and each runtime uses its own tokenizer files, so token
sequences are not proven identical.

Every query runs in two modes, and results are never mixed:

| Mode | Query content |
| --- | --- |
| `change_context` | Function, file path, detected change, old code and new code |
| `name_only` | Function name only |

The deterministic matcher uses the function name and file path. It returns an unranked
set in document order, so its Recall@k and MRR are shown with that caveat beside its set
precision and recall.

Test query categories are `exact_reference` (10), `semantic_only` (10),
`misleading_reference` (6) and `no_documentation` (12).

## Retrieval metrics

Ranking metrics use only the 26 test queries that have relevant documentation.

| Metric | Definition |
| --- | --- |
| Recall@k, k = 1, 3, 5 | Queries with a relevant section in the top k / documented queries |
| MRR | Mean of 1 / rank of the first relevant section, or 0 if none |

**Match threshold:** an embedding always ranks something, so a query counts as returning
a match only if its top cosine score reaches a threshold. Each runtime and query mode
picks its threshold from the 8 development queries: the observed score with the best
balanced accuracy, and the highest such score on a tie. It is then fixed for the test
queries. The deterministic matcher returns a match when its result set is not empty.

| Metric | Definition |
| --- | --- |
| False-positive retrieval rate | Undocumented queries that return a match / 12 |
| Abstention rate | Undocumented queries that return no match / 12 |
| False no-match rate | Documented queries that return no match / 26 |
| Score separation | Chance that a documented query's top score beats an undocumented one's, with ties as 0.5; needs no threshold |

Eight development queries provide limited threshold stability, and false-positive rates
rest on 12 queries. These results are exploratory, carry Wilson intervals and do not
establish production performance. Test queries are never used to choose thresholds.

These thresholds exist only to measure abstention in this benchmark. They are not part
of any production detection logic.

## Limitations

- The dataset is small and synthetic, so results do not estimate production accuracy.
- The same author wrote the corpus and queries. Semantic queries deliberately avoid
  naming sections, which favours embeddings over the deterministic matcher.
- Results apply only to the machine and versions recorded with them.
- The Q4 reasoning models have no full-precision reference in this benchmark.
- The 40 reasoning test cases were inspected before the Qwen3-8B run, so that comparison
  is not a fresh blind evaluation. A future prompt needs a new held-out set.
- Qwen3-8B ran with thinking off, so its thinking mode is not evaluated.
- Needs Review is not part of this benchmark's three-way schema.
