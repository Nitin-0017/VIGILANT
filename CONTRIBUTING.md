# Contributing to VIGILANT

## Set up

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

## Test

```bash
.venv/bin/pytest
```

Ordinary tests use a fake model server, so they need no model files and say nothing about model
quality. To also run the real models, set `VIGILANT_RUN_MODEL_TESTS=1`.

## Rules for changes

- **Inference stays local.** Do not add hosted APIs or API keys.
- **Models decide verdicts.** Do not add keyword or rule-based classification. Validation may
  only withhold a verdict by returning `needs_review`. It must never choose a different verdict.
- **A model failure is an error.** Never replace a failed model with another method.
- **The reasoning prompt is frozen.** A test checks its hash. Discuss any change first, and tune
  prompts only on development cases.
- **Never tune on held-out cases.** See
  [`benchmarks/README.md`](benchmarks/README.md) for the current evaluation protocol.
- **Report only measured results,** and say what the data can and cannot show.

## Pull requests

Make one focused pull request per change, from an updated `main`, with tests. CI must pass.
Workflows must pin actions to full commit SHAs and must not use `pull_request_target`.
