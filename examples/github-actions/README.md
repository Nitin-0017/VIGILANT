# GitHub Actions on a self-hosted runner

`vigilant.yml` runs VIGILANT on a pull request and publishes a job summary. The models run
on your own machine through a self-hosted runner, so no hosted inference is involved.

This setup is experimental. A passing job means the scan completed, not that the
documentation is correct. Qwen2.5-Coder-7B-Instruct is not a production-proven model.

## Choose a runner setup

A pull request from a branch in the repository can change its workflow and run any command
on the runner. No workflow setting fully prevents that, so the runner's account matters.

| Setup | Status | Separation from your data |
| --- | --- | --- |
| Dedicated standard macOS account, plus the sandbox | **Safer option** | Enforced by macOS user accounts |
| Personal account inside the sandbox | **Conditional, supervised demo only** | Only the sandbox |
| Personal account without the sandbox | **Do not use** | None |

A restricted working directory is **not** isolation. It only chooses where jobs run, and a
job can still reach anything your account can reach.

## Mandatory for every setup

- Use a **private** repository where only you can push. Never attach a personal runner to a
  public repository, including VIGILANT's own.
- Register the runner to that repository only, with `--ephemeral`.
- Run it in the foreground only while you are watching, and stop it afterward.
- Run the whole runner inside the sandbox, not just the scan. A pull request can edit the
  workflow, so a sandbox around one step protects nothing.
- Review each pull request, including `.github/`, before starting the runner.
- Install VIGILANT from a pinned commit, not in editable mode.
- Pass every security gate below first. A failed or inconclusive gate means NO-GO.

## What the sandbox can and cannot do

`sandbox-exec` is **deprecated by Apple and is not a hardened security boundary**.
`runner-sandbox.sb` reduces risk. It does not make running workflow code safe.

| Blocked when tested | Not blocked |
| --- | --- |
| Reading and listing your home directory | **Internet access:** a job can send out anything it can read |
| Fake SSH agent socket | **`localhost`:** other local services stay reachable, and denying it breaks the engine |
| Writes to your home directory and Homebrew | **Signals:** a job can kill your other processes |
| `security`, `open`, `osascript`, `launchctl`, clipboard and screen tools | **Unknown routes:** other system services may be reachable |
| Clipboard service | **Sandbox bugs:** under a personal account, these expose everything |

The runner needs `com.apple.SecurityServer` to validate TLS certificates, so that one keychain
service stays reachable. Keychain files stay unreadable, and gate 3 must show that a fake item
existing outside the sandbox is not returned inside it.

With this profile, a scan took about 70 seconds instead of 20 on a small repository, because
the blocked temporary folders appear to prevent Metal shader caching.

## Repository configuration

Replace `OWNER` with your GitHub account. Stop if any command fails.

```bash
gh repo create OWNER/vigilant-actions-test --private --disable-issues --disable-wiki
```

```bash
gh api -X PUT repos/OWNER/vigilant-actions-test/actions/permissions -F enabled=true -f allowed_actions=selected -F sha_pinning_required=true
```

```bash
gh api -X PUT repos/OWNER/vigilant-actions-test/actions/permissions/selected-actions -F github_owned_allowed=true -F verified_allowed=false
```

```bash
gh api -X PUT repos/OWNER/vigilant-actions-test/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
```

```bash
gh api -X PUT repos/OWNER/vigilant-actions-test/actions/permissions/fork-pr-workflows-private-repos -F run_workflows_from_fork_pull_requests=false
```

GitHub documents `patterns_allowed` as public-only, so it cannot limit a private repository to
one action. Any GitHub-owned action is allowed here, and it must be pinned to a full commit.

Then confirm each setting. The first must list only your account, and the next two must be
empty:

```bash
gh api repos/OWNER/vigilant-actions-test/collaborators --jq '.[].login'
```

```bash
gh secret list --repo OWNER/vigilant-actions-test
```

```bash
gh variable list --repo OWNER/vigilant-actions-test
```

```bash
gh api repos/OWNER/vigilant-actions-test/actions/permissions
```

```bash
gh api repos/OWNER/vigilant-actions-test/actions/permissions/fork-pr-workflows-private-repos
```

Copy `vigilant.yml` and `security-gates.yml` to `.github/workflows/`, and `security_gates.py` to
the repository root, then add a small Python project with Markdown documentation. Its last
commit on the default branch must change a documented function, so gate 8 starts a real scan.

## Runner setup

These steps use `/opt/vigilant-runner`. It must be outside your home folder, because the runner
requires read access to every parent folder and the sandbox denies listing your home folder. Jobs
can read it, but they can write only to its `session` folder, which is deleted before every
session. Models stay where they are and are read-only to jobs.

1. Create the folder, which needs your administrator password once, then download the runner and
   verify it:

   ```bash
   sudo mkdir -p /opt/vigilant-runner && sudo chown "$USER":staff /opt/vigilant-runner && sudo chmod 700 /opt/vigilant-runner
   ```

   ```bash
   mkdir -p /opt/vigilant-runner/bin && cd /opt/vigilant-runner && curl -fLO https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-osx-arm64-2.338.2.tar.gz
   ```

   ```bash
   cd /opt/vigilant-runner && echo "25a065af409ffa317ab4d5a3b614dd15fc9c40f2172bc1b35a1b1258e919a388  actions-runner-osx-arm64-2.338.2.tar.gz" | shasum -a 256 -c
   ```

2. Install VIGILANT from a pinned commit, not in editable mode. Use the commit you approved:

   ```bash
   python3 -m venv /opt/vigilant-runner/venv
   ```

   ```bash
   /opt/vigilant-runner/venv/bin/pip install "vigilant @ git+https://github.com/Nitin-0017/VIGILANT.git@COMMIT_SHA"
   ```

3. Create `/opt/vigilant-runner/bin/vigilant-run`, and make it executable. The sandbox gives jobs a
   different `HOME`, so this script supplies the model paths:

   ```sh
   #!/bin/sh
   models="$VIGILANT_MODEL_DIR"
   vigilant="$VIGILANT_RUNNER_DIR/venv/bin/vigilant"
   if [ "$1" = scan ]; then
     shift
     exec "$vigilant" scan \
       --coder-model "$models/qwen2.5-coder-7b-instruct-q4_k_m.gguf" \
       --embedding-model "$models/Qwen3-Embedding-0.6B-Q8_0.gguf" "$@"
   fi
   exec "$vigilant" "$@"
   ```

4. Create `/opt/vigilant-runner/runner.env`, replacing `HOME_DIR` with your home path:

   ```
   PATH=/opt/homebrew/bin:/usr/bin:/bin
   VIGILANT_RUNNER_DIR=/opt/vigilant-runner
   VIGILANT_MODEL_DIR=HOME_DIR/.cache/vigilant/models
   VIGILANT_BIN=/opt/vigilant-runner/bin/vigilant-run
   ```

5. Copy `runner-sandbox.sb` to `/opt/vigilant-runner/`.

## Running one supervised session

An ephemeral runner handles one job and then unregisters, so repeat this for each job. Each
session unpacks a fresh runner, so a job cannot leave changed runner files for the next one.

1. Plug in, and close password managers, browsers and local servers.
2. Review the pull request, and stop if `.github/` changed unexpectedly:

   ```bash
   gh pr diff NUMBER --repo OWNER/vigilant-actions-test --name-only
   ```

3. Check for queued runs, and cancel any you do not recognize:

   ```bash
   gh run list --repo OWNER/vigilant-actions-test --status queued
   ```

4. Start the runner in a terminal you keep open. This fetches a short-lived registration token
   without printing it, registers one job and runs in the foreground:

   ```bash
   cd /opt/vigilant-runner
   rm -rf session && mkdir -p session/runner session/home session/tmp
   tar xzf actions-runner-osx-arm64-2.338.2.tar.gz -C session/runner
   cp runner.env session/runner/.env
   security find-generic-password -a vigilant-canary -s vigilant-canary > /dev/null && echo VIGILANT_CANARY_KEYCHAIN_PRESENT=confirmed >> session/runner/.env
   cd session/runner
   SANDBOX=(env -i PATH=/opt/homebrew/bin:/usr/bin:/bin HOME="/opt/vigilant-runner/session/home" TMPDIR="/opt/vigilant-runner/session/tmp" LANG=en_US.UTF-8 sandbox-exec -D HOME_DIR="$HOME" -D MODEL_DIR="$HOME/.cache/vigilant/models" -D RUNNER_DIR="/opt/vigilant-runner" -f ../../runner-sandbox.sb)
   TOKEN="$(gh api -X POST repos/OWNER/vigilant-actions-test/actions/runners/registration-token --jq .token)"
   "${SANDBOX[@]}" ./config.sh --url https://github.com/OWNER/vigilant-actions-test --token "$TOKEN" --name vigilant-demo --labels vigilant --ephemeral --disableupdate --unattended --work _work
   unset TOKEN
   "${SANDBOX[@]}" ./run.sh
   ```

   The job can read this session's runner credentials and its read-only `GITHUB_TOKEN`, and it
   has internet access, so assume both can leave the machine.

5. To stop at any time, press `Ctrl-C`. To block further runs, disable Actions:

   ```bash
   gh api -X PUT repos/OWNER/vigilant-actions-test/actions/permissions -F enabled=false
   ```

6. Afterward, confirm that no runner is registered and no model server is running:

   ```bash
   gh api repos/OWNER/vigilant-actions-test/actions/runners --jq '.total_count'
   ```

   ```bash
   pgrep -fl llama-server
   ```

   ```bash
   ls -lt ~/Library/LaunchAgents | head
   ```

If anything looks wrong, stop the runner, disconnect from the network, and rotate your GitHub
credentials and any others that may be affected.

## Security gates

Run these before scanning any pull request, using `security-gates.yml` from the default branch.
**Any failed or inconclusive mandatory gate means NO-GO.** Passing shows that these probes were
blocked. It does not prove that the sandbox is secure.

| Gate | Check | Pass condition |
| --- | --- | --- |
| 1 | Job runs inside the sandbox | The process reports that it is sandboxed |
| 2 | Fake home-directory secrets | Read and list attempts are denied |
| 3 | Fake SSH agent and keychain item | The socket is refused, and the fake item is not returned |
| 4 | Clipboard and screen capture | Tools and clipboard service are refused, with no screen permission |
| 5 | Launch agent and Homebrew | Writes and `launchctl` are denied |
| 6 | Network and `localhost` | Recorded as `ASSESSED`, and you accept the risk in writing |
| 7 | GPU inference | Verbose log shows `assigned to device MTL0`, with no layers on `CPU` |
| 8 | Cancellation | A cancelled scan reports `cancelled`, and no server remains |

Also cancel one real Actions run from GitHub and confirm that no scan or server remains. Gate 8
signals the scan directly, so it cannot catch a workflow step that fails to pass the signal on. Run the gates
again after any change to macOS, the runner, the profile or `llama-server`.

Before the gates, in a normal terminal, create fake canaries only. Never use real credentials:

```bash
mkdir -p ~/.vigilant-canary/.ssh && echo FAKE > ~/.vigilant-canary/fake_secret && echo FAKE > ~/.vigilant-canary/.ssh/id_canary
```

```bash
security add-generic-password -a vigilant-canary -s vigilant-canary -w FAKE-NOT-A-SECRET
```

```bash
printf 'vigilant canary' | pbcopy
```

Confirm that the fake item exists, without printing its value. The session commands repeat this
check, and gate 3 cannot pass without it:

```bash
security find-generic-password -a vigilant-canary -s vigilant-canary > /dev/null && echo found
```

Start a fake SSH agent socket and a fake local service, and add their details to `runner.env`:

```bash
python3 -c "
import socket, threading, time
def serve(listener):
    while True:
        listener.accept()[0].close()
unix = socket.socket(socket.AF_UNIX); unix.bind('/private/tmp/vigilant-canary.sock'); unix.listen()
tcp = socket.socket(); tcp.bind(('127.0.0.1', 18999)); tcp.listen()
threading.Thread(target=serve, args=(unix,), daemon=True).start()
threading.Thread(target=serve, args=(tcp,), daemon=True).start()
time.sleep(1800)
"
```

```
VIGILANT_CANARY_DIR=HOME_DIR/.vigilant-canary
VIGILANT_CANARY_SOCKET=/private/tmp/vigilant-canary.sock
VIGILANT_CANARY_PORT=18999
VIGILANT_HOME_DIR=HOME_DIR
VIGILANT_GATE_PYTHON=/opt/vigilant-runner/venv/bin/python
```

Afterward, remove the canaries:

```bash
security delete-generic-password -a vigilant-canary -s vigilant-canary
```

```bash
rm -rf ~/.vigilant-canary /private/tmp/vigilant-canary.sock
```

## What the job status means

| Outcome | Job | Summary |
| --- | --- | --- |
| Scan completed, with any verdicts | Passes | Findings, evidence and links |
| Model, Git or scan error | Fails | States that no verdicts were produced |
| Cancelled or timed out | Cancelled or failed | States that no verdicts were produced |

The known limits in the [main README](../../README.md#known-limitations) apply here. For
example, an oversized documentation section fails the job, and reStructuredText is not read.
