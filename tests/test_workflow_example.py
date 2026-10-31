"""Checks of the example workflow's security properties, read as text.

These confirm what the file says. Only a real run can prove how GitHub applies it.
"""

import re
from pathlib import Path

WORKFLOW = (
    Path(__file__).parent.parent / "examples" / "github-actions" / "vigilant.yml"
).read_text()
RUN_BLOCKS = "\n".join(
    match[0] for match in re.finditer(r"run: \|\n(?:(?: {10}.*)?\n)+", WORKFLOW)
)


def test_only_the_approved_pull_request_events_trigger_it():
    assert "types: [opened, synchronize, reopened]" in WORKFLOW
    triggers = WORKFLOW.split("on:\n")[1].split("permissions:")[0]
    assert re.findall(r"^  (\w+):", triggers, re.MULTILINE) == ["pull_request"]
    assert "pull_request_target" not in WORKFLOW


def test_permissions_are_read_only():
    assert re.search(r"^permissions:\n  contents: read\n\n", WORKFLOW, re.MULTILINE)
    assert "write" not in WORKFLOW.split("concurrency:")[0]


def test_fork_pull_requests_are_excluded():
    expected = "if: github.event.pull_request.head.repo.full_name == github.repository"
    assert expected in WORKFLOW


def test_it_targets_only_the_labelled_self_hosted_runner():
    assert "runs-on: [self-hosted, macOS, ARM64, vigilant]" in WORKFLOW


def test_every_action_is_pinned_to_a_full_commit():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) for action in uses)


def test_checkout_uses_the_head_commit_full_history_and_no_saved_credentials():
    assert "ref: ${{ github.event.pull_request.head.sha }}" in WORKFLOW
    assert "fetch-depth: 0" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW


def test_shell_scripts_never_interpolate_github_expressions():
    assert RUN_BLOCKS
    assert "${{" not in RUN_BLOCKS


def test_scan_compares_from_the_merge_base():
    assert 'merge_base="$(git merge-base "$BASE_SHA" "$HEAD_SHA")"' in RUN_BLOCKS
    assert '"$VIGILANT_BIN" scan "$merge_base" "$HEAD_SHA"' in RUN_BLOCKS


def test_vigilant_comes_from_the_runner_and_is_never_installed_from_the_pull_request():
    assert "pip install" not in WORKFLOW
    assert "$VIGILANT_BIN" in RUN_BLOCKS


def test_summary_is_published_even_when_the_scan_fails_or_is_cancelled():
    assert "if: always()" in WORKFLOW
    assert "The scan did not finish" in WORKFLOW
    assert '>> "$GITHUB_STEP_SUMMARY"' in WORKFLOW


def test_duplicate_runs_for_a_pull_request_are_cancelled_and_time_is_limited():
    assert "group: vigilant-pr-${{ github.event.pull_request.number }}" in WORKFLOW
    assert "cancel-in-progress: true" in WORKFLOW
    assert re.search(r"timeout-minutes: \d+", WORKFLOW)


def test_it_does_not_edit_or_commit_anything():
    assert "git commit" not in WORKFLOW
    assert "git push" not in WORKFLOW
