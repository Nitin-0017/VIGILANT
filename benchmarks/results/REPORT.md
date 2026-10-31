# Local model benchmark report

**Conclusion:** all three models ran locally on all test data with no failures. The
project owner selected these candidates for VIGILANT:

| Role | Model | Status |
| --- | --- | --- |
| Reasoning | Qwen2.5-Coder-7B-Instruct | **Selected candidate, not production-ready** |
| Reasoning | Qwen3-8B | Benchmarked alternative, not selected |
| Retrieval | Qwen3-Embedding-0.6B | **Selected** |
| Baseline | Deterministic matcher | Isolated benchmark baseline only, not AI |

Qwen3-Embedding-0.6B retrieved documentation well on this small synthetic set.
Qwen2.5-Coder-7B-Instruct found every drift case but also flagged most correct or
unrelated documentation. Its classification accuracy and false-positive performance need
improvement and a fresh held-out evaluation. No model is integrated into VIGILANT yet.

## Scope and conditions

| Item | Value |
| --- | --- |
| Dataset | Version 1.0, `eacb7e364219e19d5ee796717c7b64ebed653803a5f1fd1f1016537b2d50e23c` |
| Provenance | Synthetic; drafted by Claude Code, then reviewed by the project owner |
| Reasoning prompt | `a0c4f420b12c06d037a59526765ab261bbce29af9b1afdc77e45f05935efcba6`, run once, not tuned |
| Machine | Apple M4, 16 GB unified memory, macOS 26.0.1 |
| Runtimes | llama.cpp 0.8.0, torch 2.15.0, transformers 5.22.1, sentence-transformers 6.2.0 |
| Inference | Local only; no external API or key |

Conditions that limit these results:

- **Power:** the Mac was on battery, and Low Power Mode was on when checked straight after
  the runs. Speeds may understate what this machine can do on mains power.
- **Warm cache:** models had been loaded earlier, so load times are not cold starts.
- **Held-out exposure:** before this run, tests executed the matcher on the semantic and
  no-documentation retrieval queries and ran metric code on fake vectors. No real model
  ran, nothing was tuned, and those tests were then rewritten to use synthetic data.
- **Test set now seen:** these errors have been read, so this set can no longer judge a
  future prompt as held-out data.
- **Small sample:** results are exploratory and do not establish production performance.

## Reasoning: Qwen2.5-Coder-7B-Instruct (Q4_K_M)

All 40 test cases completed with 0 failures and 0 invalid responses.

| Metric | Result | 95% Wilson interval |
| --- | --- | --- |
| Precision | 0.548 (17/31) | 0.378-0.708 |
| Recall | 0.944 (17/18) | 0.742-0.990 |
| F1 | 0.694 | Not computed |
| Three-way accuracy | 0.600 (24/40) | 0.446-0.737 |
| Evidence, correctly detected drift | 0.538 (7/13) | 0.291-0.768 |
| Evidence, all actual drift | 0.538 (7/13) | 0.291-0.768 |
| Unsupported claim rate | 0.275 (11/40) | 0.161-0.428 |
| Valid JSON, schema enforced | 1.000 (40/40) | 0.912-1.000 |

| Expected | Predicted `drift` | Predicted `missing_documentation` | Predicted `no_drift` |
| --- | --- | --- | --- |
| `drift` (13) | 13 | 0 | 0 |
| `missing_documentation` (5) | 1 | 3 | 1 |
| `no_drift` (22) | 14 | 0 | 8 |

| Category | Correct |
| --- | --- |
| Changed default | 5/6 |
| Renamed parameter | 5/6 |
| Changed behavior | 5/6 |
| No drift | 4/6 |
| Missing documentation | 3/5 |
| Prompt injection | 1/2 |
| Similar name | 1/5 |
| Semantically related | 0/4 |

Findings:

- **Main weakness:** the model got 1 of 9 unrelated-section cases right, usually treating
  a similar name, topic or number as the same subject.
- **Dropped backticks:** every quote that should begin with a backtick lost it, which fails
  exact matching. Under the approved rule evidence is 7/13. Ignoring backticks, as a
  post-hoc figure only, it is 13/13. Ten of the 11 unsupported-claim cases have the same
  cause, leaving 1 (`nd-6`). The cause was not established.
- **Prompt injection:** `pi-1` correctly reported drift despite the embedded instruction.
  `pi-2` wrongly reported drift, as the embedded instruction asked, but its explanation
  cited the timeout, so this does not show the instruction was followed.
- **Manual review:** one reader (Claude Code, not independent) read all 40 explanations
  and found clear false statements in 5 cases: `cd-5`, `rn-6`, `bh-5`, `nd-4` and `nd-6`.
  This count is separate from the automatic rate. Many other false positives state true
  facts but wrongly connect two different subjects.
- **Not tested:** JSON without schema enforcement, CPU-only speed, uncertainty or Needs
  Review, and any reasoning run on the development split.

## Retrieval: Qwen3-Embedding-0.6B

All 38 test queries completed in both modes and both runtimes. Input hashes match:
`86b1123518d641832c78318bfb466b1e094017a203ef16e0de7edbf77d8072b1`.

This is a combined runtime and precision comparison, so any difference may come from
either source. Thresholds come from 8 development queries, which give limited stability.

| Metric | Sentence Transformers, name only | Q8 GGUF, name only | Sentence Transformers, context | Q8 GGUF, context |
| --- | --- | --- | --- | --- |
| Recall@1 | 0.731 | 0.731 | 0.923 | 0.923 |
| Recall@3 | 0.846 | 0.846 | 1.000 | 1.000 |
| Recall@5 | 0.885 | 0.885 | 1.000 | 1.000 |
| MRR | 0.809 | 0.809 | 0.955 | 0.955 |
| Threshold from dev | 0.4478 | 0.4482 | 0.5093 | 0.5064 |
| False-positive rate (of 12) | 0.167 | 0.167 | 0.083 | 0.083 |
| Abstention rate (of 12) | 0.833 | 0.833 | 0.917 | 0.917 |
| False no-match rate (of 26) | 0.038 | 0.038 | 0.077 | 0.038 |
| Score separation | 0.955 | 0.952 | 0.994 | 0.994 |

Recall@1 and MRR by category, with change context:

| Category | Sentence Transformers | Q8 GGUF |
| --- | --- | --- |
| Exact reference (10) | 1.000 / 1.000 | 1.000 / 1.000 |
| Semantic only (10) | 0.900 / 0.933 | 0.900 / 0.933 |
| Misleading reference (6) | 0.833 / 0.917 | 0.833 / 0.917 |

The runtimes returned the same top section on 37/38 name-only and 38/38 context queries.
Change context clearly improved retrieval on these queries.

## Deterministic matcher baseline

This is a benchmark-only baseline and not an AI result. It is unranked, so its Recall@k
and MRR reflect document order.

| Metric | Result |
| --- | --- |
| Recall@1, @3, @5 and MRR | 0.423 |
| Set precision | 1.000 (11/11) |
| Set recall | 0.423 (11/26) |
| False-positive rate (of 12) | 0.000 |
| Abstention rate (of 12) | 1.000 |
| False no-match rate (of 26) | 0.577 |
| Exact reference, Recall@1 | 1.000 |
| Semantic only, Recall@1 | 0.000 |
| Misleading reference, Recall@1 | 0.167 |

The semantic queries avoid naming their functions by design, which favours embeddings.

## Hardware and performance

| Measurement | Coder 7B Q4 | Embedding, Sentence Transformers | Embedding, Q8 GGUF |
| --- | --- | --- | --- |
| Model files | 4.68 GB | 1.19 GB weights | 0.64 GB |
| Load time | 5.65 s | 7.58 s, including imports | 5.75 s |
| Peak RSS | 4,017 MB | 686 MB | 7,808 MB |
| Peak footprint | 619 MB | 1,732 MB | 7,321 MB |
| Mean CPU of the process | 4.0% | 18.7% | 10.0% |
| Whole benchmark step | 279 s | 16 s | 14 s |

| Speed | Result |
| --- | --- |
| Coder median and maximum latency | 6.77 s and 11.12 s per case |
| Coder median output and prompt speed | 14.1 and 117.2 tokens/s |
| Sentence Transformers, per section | 40.4 ms |
| Sentence Transformers, per query | 15.9 ms name only, 73.7 ms context |
| Q8 GGUF, per section | 56.7 ms |
| Q8 GGUF, per query | 22.1 ms name only, 74.2 ms context |

Reading these figures:

- Memory figures do not reliably capture Metal or memory-mapped allocations, so neither
  column alone is total memory. The Coder footprint is clearly too low for its model.
- The Q8 GGUF memory reflects the benchmark's `llama-server` settings of 8 slots at 8192
  tokens each, not the model's minimum.
- GPU utilization was not measured, because it needs `sudo powermetrics`.

Total Python environment disk use is 1.1 GB, and model files use 6.1 GB. All three
models were practical to run on this machine.

## Recommendation

| Model | Runs locally | Suitability |
| --- | --- | --- |
| Qwen3-Embedding-0.6B | Yes, both runtimes | Selected. Carry into integration using change context, and confirm on real repository documentation |
| Q8 GGUF runtime | Yes | Matched the full-precision ranking metrics here without PyTorch; lower its server memory settings first |
| Qwen2.5-Coder-7B-Instruct | Yes | Selected candidate. Practical to run, but **not yet reliable for final classification** because of false positives on unrelated sections |
| Qwen3-8B | Yes | Not selected. Thinking mode was not evaluated |

Before relying on the selected reasoning model, tune its prompt on development data only,
resolve the dropped backticks, evaluate Needs Review, and test on a new held-out set.

These results do not apply to GitHub Actions runners, and the dataset is too small and
synthetic to estimate production accuracy.

## Reasoning comparison: Qwen2.5-Coder-7B-Instruct and Qwen3-8B

**Decision:** the project owner selected Qwen2.5-Coder-7B-Instruct. Qwen3-8B stays in this
report as an evaluated alternative. The selection rests on better three-class accuracy
(60.0% vs 47.5%), precision (54.8% vs 51.4%) and F1 (69.4% vs 67.9%), fewer false drift
classifications (14 vs 17), and lower median latency and peak memory in this benchmark.

The difference is not statistically established, and selection does not mean production
readiness. Neither model is ready for final classification. Qwen3-8B had higher recall
and lower maximum latency, and the models' evidence scores were effectively tied.

**This is not a fresh blind evaluation.** The 40 test cases and the Qwen2.5-Coder-7B
errors had already been inspected before this run. The prompt was not changed in
response, but this set is no longer truly held out.

| Setting | Both models unless noted |
| --- | --- |
| Test cases and labels | Same 40 cases, dataset `eacb7e36...` |
| Prompt | Byte-identical, `a0c4f420...`; not tuned |
| Generation | Temperature 0, seed 42, 400 max tokens, 8192 context, JSON schema enforced |
| Quantization | Q4_K_M for both |
| Qwen2.5 file | Official GGUF, `509287f7...` |
| Qwen3 file | Official `Qwen/Qwen3-8B-GGUF`, `d98cdcbd...`, 8.19 billion parameters |
| Qwen3 thinking | Off, so the template inserts an empty think block |
| Conditions | Same machine, on battery with Low Power Mode on; both models had loaded earlier |

Thinking was turned off for comparability and because the Qwen3 model card warns against
greedy decoding in thinking mode. These results do not show how Qwen3-8B performs with
thinking on, or with the model card's suggested sampling settings.

| Metric | Qwen2.5-Coder-7B | Qwen3-8B | Better |
| --- | --- | --- | --- |
| Precision | 0.548 (17/31) | 0.514 (18/35) | Qwen2.5 |
| Recall | 0.944 (17/18) | 1.000 (18/18) | Qwen3 |
| F1 | 0.694 | 0.679 | Qwen2.5 |
| Three-way accuracy | 0.600 (24/40) | 0.475 (19/40) | Qwen2.5 |
| `no_drift` cases called `drift` | 14/22 | 17/22 | Qwen2.5 |
| Total `drift` predictions (13 are real) | 28 | 34 | Qwen2.5 |
| Evidence correctness, approved rule | 0.538 (7/13) | 0.615 (8/13) | Qwen3 |
| Evidence ignoring backticks (post-hoc) | 13/13 | 12/13 | Qwen2.5 |
| Unsupported claim rate, approved rule | 0.275 (11/40) | 0.350 (14/40) | Qwen2.5 |
| Unsupported, not explained by backticks (post-hoc) | 1/40 | 0/40 | Qwen3 |
| Invalid JSON rate | 0/40 | 0/40 | Tie |
| Median latency | 6.77 s | 7.45 s | Qwen2.5 |
| Maximum latency | 11.12 s | 10.26 s | Qwen3 |
| Total inference time | 273.0 s | 300.5 s | Qwen2.5 |
| Median output speed | 14.1 tokens/s | 14.0 tokens/s | Tie |
| Model file | 4.68 GB | 5.03 GB | Qwen2.5 |
| Load time, warm cache | 5.65 s | 2.08 s | Qwen3 |
| Peak RSS | 4,017 MB | 6,055 MB | Qwen2.5 |
| Peak footprint | 619 MB | 1,335 MB | Qwen2.5 |

95% Wilson intervals for Qwen3-8B: precision 0.356-0.670, recall 0.824-1.000, accuracy
0.329-0.625, evidence 0.355-0.823, unsupported claims 0.221-0.505. They overlap the
Qwen2.5 intervals. The models disagreed on three-way correctness in 9 cases: Qwen2.5 alone
was right in 7 and Qwen3 alone in 2 (exact McNemar test, two-sided p = 0.18).

Qwen3-8B confusion matrix:

| Expected | Predicted `drift` | Predicted `missing_documentation` | Predicted `no_drift` |
| --- | --- | --- | --- |
| `drift` (13) | 13 | 0 | 0 |
| `missing_documentation` (5) | 4 | 1 | 0 |
| `no_drift` (22) | 17 | 0 | 5 |

| Category | Qwen2.5-Coder-7B | Qwen3-8B |
| --- | --- | --- |
| Changed default | 5/6 | 4/6 |
| Renamed parameter | 5/6 | 4/6 |
| Changed behavior | 5/6 | 5/6 |
| No drift | 4/6 | 3/6 |
| Missing documentation | 3/5 | 1/5 |
| Prompt injection | 1/2 | 1/2 |
| Similar name | 1/5 | 1/5 |
| Semantically related | 0/4 | 0/4 |

Manual review by one reader (Claude Code, not independent) found clear false or
self-contradictory explanations in 6 Qwen3-8B cases: `cd-6`, `rn-5`, `rn-6`, `nd-1`, `nd-2`
and `sn-1`. Several explain that the documentation is correct or irrelevant and still
report `drift`. The Qwen2.5-Coder-7B count from the same kind of review was 5.

Memory figures do not reliably capture Metal or memory-mapped allocations, and load
times depend on how recently each file was read.

## Result file integrity

After the runs, the `settings.model` field in the two reasoning result files was changed
from an absolute local path to the model filename, to avoid publishing a local username.
A parsed comparison confirmed that nothing else changed, including every metric and raw
model response. These are the SHA-256 hashes:

| File | As generated | After redaction |
| --- | --- | --- |
| `reasoning.json` | `f9c297d249c7badf78d1f491aadc234f20e62b5001a3c5c5c242e18275fbc7ee` | `f3ce08b98fdeccd48bfc790df987b83092588eb922c5285da4d153438ed6196b` |
| `reasoning-qwen3-8b.json` | `c8cabfa223a4c7a1c812c42bb05416f0cac7aae5dd047eae01a43b1feac5c6d2` | `5e8bbda39c510f282e6a4c8c9a35481ed73e3cf466d9c2776ca8185ef8e957b5` |

The other three result files were not edited. `run_reasoning.py` still records the path it
is given, so check a new result file for local paths before publishing it.

## Production request shape and prompt development (PR 5)

This work used 34 new development cases in `benchmarks/dataset/dev_cases.json`, drafted by
Claude Code, with SHA-256
`b1142204d8a33910b9c1b2d3b2cbfa6d185983e8215e05d3605563f26c6a18d3`. Each case has three
sections. **These cases guided decisions, so these are not held-out results, and they do
not establish production accuracy.** PR 4 test cases were not used. All runs used battery
power with Low Power Mode on. Verdicts have four outcomes, and a function whose sections
are all unrelated should be `needs_review`, so these figures are not comparable with PR 4.

### One request per function, or one per section

Both shapes used the same first prompt (`b7a10169...`). Each section request needs a rule to
combine verdicts, which exists only in the benchmark.

| Measure | One request per function | One request per section |
| --- | --- | --- |
| Accuracy | 0.735 (25/34) | 0.588 (20/34) |
| False drift | 1 | 10 |
| Unrelated-section cases correct | 6/7 | 1/7 |
| Evidence correct | 21/27 | 23/27 |
| Model calls | 34 | 102 |
| Total inference time | 297.4 s | 664.8 s |
| Median seconds per function | 8.87 | 18.83 |
| Prompt tokens per call, median and maximum | 455.5 and 492 | 403.5 and 432 |
| Total prompt tokens | 15,568 | 41,240 |
| Largest prompt, share of 8192 context | 6.0% | 5.3% |

**Decision:** production sends one request per function. It was more accurate, had far
fewer false drift results, took less than half the time and needs no combining rule. The
per-section shape scored 2 more evidence cases, but mainly because it called `drift` far
more often. With the selected prompt, per-function evidence is correct on all 15 drift
cases. Token counts come from tokenizing the prompts, because the first runs' `prompt_n`
field counted only uncached tokens.

### Prompt revisions

| Version | Prompt hash | Accuracy | False drift | Correct `no_drift` | Unrelated-section cases correct | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| v1 | `b7a10169...` | 0.735 | 1 | 3/7 | 6/7 | 21/27 |
| v2 | `a3b5998e...` | 0.765 | 1 | 5/7 | 5/7 | 22/27 |
| **v3, selected** | `e92faada...` | 0.735 (0.569-0.854) | 2 | 0/7 | 6/7 | 20/27 |
| v3 without code evidence for `no_drift`, rejected | `24af4a81...` | 0.706 | 2 | 6/7 | **0/7** | 25/27 |

| Selected prompt, by expected verdict | `drift` | `missing_documentation` | `no_drift` | `needs_review` |
| --- | --- | --- | --- | --- |
| `drift` (15) | 15 | 0 | 0 | 0 |
| `missing_documentation` (5) | 0 | 4 | 0 | 1 |
| `no_drift` (7) | 1 | 0 | 0 | 6 |
| `needs_review` (7) | 1 | 0 | 0 | 6 |

**The selection puts safety above usefulness.** The rejected variant looks better on
correct `no_drift` answers, but it turned 6 unrelated-section cases into silent `no_drift`.
The project forbids classifying uncertainty as `no_drift`, so that requires v3 with strict
validation. The cost is that correct `no_drift` answers also become `needs_review`, because
the model tends to leave code evidence empty for that verdict. This validation works partly
by coincidence, not because the model understands that a section is unrelated, and a fresh
evaluation may not reproduce it.

Accepting a literal `\n` in code evidence was also rejected: on stored responses it let
more unrelated-section cases through as `no_drift`.

### Production resource measurements

| Measurement | Result |
| --- | --- |
| Embedding server load time | 1.06 s, warm cache |
| Embedding server peak RSS and footprint | 1,637 MB and 1,015 MB, down from 7,321 MB footprint in PR 4 |
| Embedding speed | 23.9 ms per section, across 54 sections |
| Reasoning load time | 1.82 s, warm cache |
| Reasoning peak RSS and footprint | 5,062 MB and 662 MB |
| Reasoning median and maximum per function | 9.58 s and 12.64 s |

Memory figures do not reliably capture Metal or memory-mapped allocations.

### Still required

The fresh held-out evaluation has not run, because its test cases should not be written by
the author of these prompts. Nothing here shows improved accuracy over PR 4.
