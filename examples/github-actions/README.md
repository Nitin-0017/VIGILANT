# GitHub Actions on a self-hosted runner

`vigilant.yml` runs VIGILANT on a pull request and publishes a job summary. The models run
on your own machine through a self-hosted runner, so no hosted inference is involved.

This setup is experimental. A passing job means the scan completed, not that the
documentation is correct. Qwen2.5-Coder-7B-Instruct is not a production-proven model.

## Security requirements

A pull request from a branch in the repository can change this workflow and run any command
on the runner. No workflow setting fully prevents that, so these requirements are mandatory:

- Use a **private** repository where only people you trust can push. Never attach a personal
  runner to a public repository.
- Run the runner under a **dedicated standard macOS account** with no personal credentials.
- Make your own home directory private. macOS commonly adds local users to the `staff` group,
  so a home directory with group access may be readable by the runner account.
- Keep the model files in a location the runner can read but not change.
- Register the runner to one repository only, and run it in the foreground only during use.

The workflow excludes pull requests from forks, has read-only permissions, keeps no Git
credentials after checkout and never uses `pull_request_target`.

## Setup

These steps change your machine and need an administrator, so review each one first.

1. In **System Settings > Users & Groups**, add a standard account for the runner.
2. Make your home directory private and share the models read-only:

   ```bash
   chmod 700 ~
   ```

   ```bash
   sudo mkdir -p /Users/Shared/vigilant-models
   ```

   ```bash
   sudo cp ~/.cache/vigilant/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf ~/.cache/vigilant/models/Qwen3-Embedding-0.6B-Q8_0.gguf /Users/Shared/vigilant-models/
   ```

   ```bash
   sudo chmod -R a=rX /Users/Shared/vigilant-models
   ```

3. Under the runner account, confirm that the GPU is available. Look for an `MTL0` device:

   ```bash
   /opt/homebrew/bin/llama-server --list-devices
   ```

   If it is missing, do not move the runner to your own account. Report it and choose
   another isolated option.

4. Under the runner account, install VIGILANT in its own environment from a pinned commit,
   not in editable mode.
5. Register the runner to the private repository with the labels `self-hosted`, `macOS`,
   `ARM64` and `vigilant`, using the steps GitHub shows in the repository's runner settings.
6. Add these to the runner's `.env` file, replacing the placeholder:

   ```
   PATH=/opt/homebrew/bin:/usr/bin:/bin
   VIGILANT_BIN=<runner environment>/bin/vigilant
   ```

7. The workflow's scan step needs model paths for this location. Add these options to the
   `scan` command in your copy of `vigilant.yml`:

   ```
   --coder-model /Users/Shared/vigilant-models/qwen2.5-coder-7b-instruct-q4_k_m.gguf
   --embedding-model /Users/Shared/vigilant-models/Qwen3-Embedding-0.6B-Q8_0.gguf
   ```

8. Copy `vigilant.yml` to `.github/workflows/` in the private repository. To limit which
   files are analyzed, set the repository variable `VIGILANT_CODE_PATHS` to
   space-separated paths such as `src lib`.
9. Start the runner with `./run.sh` only when needed, and stop it afterward.

## What the job status means

| Outcome | Job | Summary |
| --- | --- | --- |
| Scan completed, with any verdicts | Passes | Findings, evidence and links |
| Model, Git or scan error | Fails | States that no verdicts were produced |
| Cancelled or timed out | Cancelled or failed | States that no verdicts were produced |

The known limits in the [main README](../../README.md#known-limitations) apply here. For
example, an oversized documentation section fails the job, and reStructuredText is not read.
