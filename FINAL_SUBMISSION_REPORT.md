# VIGILANT final submission report

**Team:** Chakravyuh (Nitin Kumar, Manjeet, Kunal Kumar Mandal)

**Status:** VIGILANT runs end to end on an Apple M4 with local models, and it has analyzed a
real pull request through GitHub Actions. **Its accuracy is not established**, because the
held-out evaluation has not been run. Nothing in this report is a production accuracy claim.

## 1. Problem statement

Problem 5: Automated Documentation Drift Detection. Code changes while its documentation
stays the same, so the documentation becomes wrong without anyone noticing.

## 2. Solution overview

Between two Git revisions, VIGILANT finds the Python functions that changed, retrieves the most
relevant Markdown documentation, and asks a local AI model for a verdict backed by quoted
evidence. All inference runs on the local machine, with no hosted APIs or API keys.

| Verdict | Meaning |
| --- | --- |
| `drift` | A statement in a relevant section is false for the new code |
| `missing_documentation` | A relevant section stays true but is silent about a new parameter, error or operation |
| `no_drift` | A relevant section describes the function and stays true |
| `needs_review` | The model could not establish a verdict, or its answer lacked supporting evidence |

## 3. Architecture and pipeline

1. **Find changes.** Git and Python's `ast` module identify changed functions and their line
   ranges at both revisions.
2. **Extract documentation.** Markdown files at the head revision are split into sections.
3. **Retrieve.** The embedding model ranks sections for each function. The top 3 are kept, with
   no score threshold.
4. **Reason.** The reasoning model receives one request per function and answers against a JSON
   schema, at temperature 0 and seed 42.
5. **Validate.** Every quote must exist in the retrieved section or changed code. Otherwise the
   verdict becomes `needs_review`. Validation can only withhold an answer, not choose a verdict.
6. **Report.** Results go to text, JSON or a GitHub job summary, with locations verified against
   Git.

**No fallback exists.** If a model is missing or fails, the scan stops with an error. No keyword
or rule-based method replaces the models. A deterministic matcher exists only as an isolated
benchmark baseline.

`llama-server` listens on `127.0.0.1` on a random port. VIGILANT starts it for each model in
turn and stops it when that step ends or the scan is cancelled.

## 4. Model details

| Role | Model | File | Size |
| --- | --- | --- | --- |
| Retrieval | Qwen3-Embedding-0.6B (Q8) | `Qwen3-Embedding-0.6B-Q8_0.gguf` | 0.64 GB |
| Reasoning | Qwen2.5-Coder-7B-Instruct (Q4_K_M) | `qwen2.5-coder-7b-instruct-q4_k_m.gguf` | 4.68 GB |

- Qwen2.5-Coder-7B-Instruct is a **selected candidate, not a production-proven model**.
- The reasoning prompt is frozen at SHA-256
  `e92faada79520b502fc573fac34542c3d1a526b217a1aa0eda6614ef80e4c818`. This was rechecked against
  the code for this report.
- Pinned model revisions and hashes are in [`benchmarks/README.md`](benchmarks/README.md).

## 5. Local execution

These commands install from scratch:

```bash
brew install llama.cpp
```

```bash
python3 -m venv ~/.cache/vigilant/venv
```

```bash
~/.cache/vigilant/venv/bin/pip install .
```

Put both model files in `~/.cache/vigilant/models`. VIGILANT never downloads models. To scan,
run this inside a Git repository:

```bash
~/.cache/vigilant/venv/bin/vigilant scan BASE HEAD --report-file report.json
```

There is no server to start first, because `scan` starts and stops `llama-server` itself.

## 6. Demonstration results

Environment: Apple M4 with 16 GB of unified memory, llama.cpp 0.8.0 reporting
`MTL0: Apple M4`.

The fixtures are three small repositories written by Claude Code for this check. **They are not
independent and do not measure accuracy.** Together they contain 10 changed functions.

| Function | Scenario | Final verdict | Model's answer | Assessment |
| --- | --- | --- | --- | --- |
| `connect` | Default timeout 30 to 90, docs say 30 | `drift` | `drift` | Correct |
| `send_invoice` | New `currency` parameter | `missing_documentation` | same | Reasonable, but cites only the `# Client` heading |
| `sync_inventory` | New `dry_run` parameter | `missing_documentation` | same | Correct |
| `normalize_email` | Refactor, docs still correct | `needs_review` | `no_drift`, no code evidence | Conservative |
| `apply_discount` | Refactor, docs still correct | `needs_review` | `no_drift`, no code evidence | Conservative |
| `parse_port` | Refactor, docs still correct | `needs_review` | `no_drift`, no code evidence | Conservative |
| `greet` | Refactor, docs still correct | `needs_review` | `no_drift`, no code evidence | Conservative |
| `connect` (second fixture) | Code and docs updated together | `needs_review` | `no_drift`, no code evidence | Conservative |
| `fetch_page` | Code and docs updated together | `needs_review` | `no_drift`, no code evidence | Conservative |
| `process_batch` | Vague doc, code now sorts items | `drift` | `drift` | **Questionable:** speculates about performance |

Example output:

```
client.py:1 connect: drift
  change: timeout: 30 -> 90
  documentation README.md:5: `connect` opens a socket to the host. The default timeout is 30 seconds.
  code: timeout=90
  explanation: The documentation states that the default timeout is 30 seconds, but the new code sets the default timeout to 90 seconds.
```

- **A final `no_drift` never appeared.** The model answered `no_drift` 6 times, always with empty
  code evidence, so strict validation turned each into `needs_review`. This validation is
  deliberate: relaxing it let unrelated-section cases through as silent `no_drift` in development.
- **Repeatability:** a second run of the first fixture returned identical verdicts and raw model
  answers. It took 35 seconds in total, and reasoning took about 5 to 8 seconds per function.
- **Metal:** server logs showed model layers on `MTL0` and none on `CPU`, for both models.

## 7. Functional evaluation

Each cited location in the three reports was compared with `git show` by a separate script, with
**0 wrong locations**.

Failure handling was tested with a fake server returning chosen answers, so these rows test
VIGILANT's validation and not the model:

| Injected answer or failure | Result |
| --- | --- |
| Valid answer, as a control | `drift` kept only for the function it applied to |
| Not JSON, or an unknown verdict | `needs_review` |
| Fabricated documentation quote | `needs_review` |
| Fabricated code evidence | `needs_review` |
| No documentation evidence | `needs_review` |
| `no_drift` with empty code evidence | `needs_review` |
| Model itself answers `needs_review` | `needs_review`, with the model's reason |
| Malformed response, HTTP 500, or server crash | Exit 1, report `failed`, no verdicts |

These failures ran through the real CLI:

| Failure | Result |
| --- | --- |
| Missing model file or missing `llama-server` | Exit 1, report `failed` with the exact error |
| Invalid revision | Exit 1, report `failed` with the exact error |
| Corrupt model file, with real `llama-server` | Exit 1, report `failed` with the exact error |

### Benchmarks

These figures come from small synthetic data drafted by Claude Code and reviewed by the project
owner. Details are in [`benchmarks/results/REPORT.md`](benchmarks/results/REPORT.md).

| Measure | Data | Result |
| --- | --- | --- |
| Reasoning three-way accuracy, older prompt | 40 cases | 0.600 (24/40), 95% interval 0.446-0.737 |
| Drift precision and recall, older prompt | 40 cases | 0.548 (17/31) and 0.944 (17/18) |
| Accuracy, frozen prompt | 34 development cases | 0.735 (25/34), interval 0.569-0.854 |
| Retrieval Recall@3, with change context | 26 of 38 queries | 1.000 |

The 40 cases have since been inspected, and the 34 cases guided prompt decisions. **Neither is a
held-out result.** The frozen prompt was never scored on the 40 cases, so the two accuracy figures
are not comparable and show no improvement.

### Evaluation still pending

The fresh 60-case held-out evaluation has not been run. The team must write or independently
approve its labels, because the author of the prompts should not write them.

## 8. Test results

Run on 2026-11-01 against the final uncommitted working tree:

| Run | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| Full test suite | 315 | 0 | 4 |
| Targeted workflow and security gate tests, part of the full suite | 33 | 0 | 0 |
| The 4 skipped real-model tests, run separately | 4 | 0 | 0 |

Passing tests show that the code behaves as designed. They do not measure detection accuracy.

## 9. GitHub Actions integration

The workflow is in [`examples/github-actions/`](examples/github-actions/README.md). It runs on
`pull_request`, with read-only permissions, a pinned `actions/checkout`, a guard against fork pull
requests, and no `pull_request_target`. The setup guide covers the runner, sandbox and gates.

| Item | Status | Evidence |
| --- | --- | --- |
| Workflow statically validated | **Yes** | YAML parses; triggers, permissions and pinning checked. `actionlint` was not available |
| Workflow executed | **Yes** | Private test repository, runs on 2026-10-31 |
| Runner registered | **Yes, four separate sessions** | Ephemeral; each unregistered after its job |
| Runner online now | **No, by design** | 0 runners registered; it runs only during supervised sessions |
| Real PR scan completed | **Yes** | Run 41253307389, attempt 2, conclusion `success` |
| Summary uploaded | **Yes, per the runner log** | `success rate: 5/5` on upload |
| Rendered summary viewed | **Not confirmed** | Private repository and no API for summaries; a signed-in person must open it |

The PR scan found `connect` as `drift`, citing `README.md:5` against `timeout=90`.

**Security gates**, run in a real job on the sandboxed runner, using fake canaries only:

| Gate | Result |
| --- | --- |
| Job runs inside the sandbox | PASS |
| Fake home secrets | PASS |
| Fake SSH agent | PASS |
| Fake keychain item | PASS |
| Clipboard and screen capture | PASS |
| Launch agent and Homebrew writes | PASS |
| Network and `localhost` | ASSESSED: both reachable, and this risk was accepted by the project owner |
| Metal inference for both models | PASS |
| Cancellation of a scan | PASS |

Passing shows only that these probes were blocked. `sandbox-exec` is deprecated and is not a
hardened security boundary.

**Cancellation bug found and fixed.** In the workflow merged in PR 6, cancelling a run signals
only the step's shell, so the scan and its model server keep running. The fix runs the scan with
`exec`. The results:

| Check | Merged workflow | Fixed workflow |
| --- | --- | --- |
| Real Actions run cancelled | Scan and server still running afterward | No scan or server for 30 seconds afterward |
| Local reproduction with a fake server | Scan and server remain | Report `cancelled`, none remain |

The fix had not been merged upstream when this report was written. A job killed outright with
`SIGKILL` cannot clean up its server, and cancellation by a job timeout was not tested separately.

## 10. Limitations

- **Accuracy is not established.** Every figure here comes from small synthetic data or fixtures.
- **`no_drift` effectively does not appear.** Correct documentation usually shows `needs_review`.
- **False positives are a known weakness,** especially for similar names and unrelated sections.
- **Evidence can be real but weak.** Validation confirms that a quote exists, not that it supports
  the verdict.
- **Retrieval has no cut-off,** so unrelated sections can reach the model.
- **Scope is Python functions and Markdown only.** Renames appear as a removal plus an addition.
- **Large inputs can stop a scan** when they exceed the embedding context.
- **The runner is a supervised demo setup.** A job can reach the internet and `localhost`, signal
  the user's other processes, and read its own session's credentials. `com.apple.SecurityServer`
  stays reachable because the runner needs it for TLS. A dedicated macOS account remains safer.

More detail is in the [README](README.md#known-limitations).

## 11. Future scope

- Run the held-out evaluation with independently written labels.
- Make correct documentation reach `no_drift` without letting unrelated sections through.
- Reduce false positives on similar names and unrelated sections.
- Check that evidence supports a verdict, not just that it exists.
- Support reStructuredText, more languages and linked renames.
- Replace `sandbox-exec` with stronger isolation for shared use.


