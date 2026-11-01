"""Checks of the runner gate logic. These do not show that any real runner is secure."""

import importlib.util
import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent.parent / "examples" / "github-actions"
spec = importlib.util.spec_from_file_location(
    "security_gates", EXAMPLES / "security_gates.py"
)
security_gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security_gates)

PROFILE = (EXAMPLES / "runner-sandbox.sb").read_text()
WORKFLOW = (EXAMPLES / "security-gates.yml").read_text()
README = (EXAMPLES / "README.md").read_text()


def result(status):
    return {"gate": "x", "status": status, "detail": ""}


@pytest.mark.parametrize("status", ["FAIL", "INCONCLUSIVE"])
def test_any_failed_or_inconclusive_gate_means_no_go(status):
    results = [result("PASS"), result("ASSESSED"), result(status)]

    assert security_gates.decide(results) == "NO-GO"


def test_decision_passes_only_when_every_gate_passed_or_was_assessed():
    assert security_gates.decide([result("PASS"), result("ASSESSED")]) == "GATES PASSED"


def test_a_gate_that_crashes_is_inconclusive_not_passed():
    def crash():
        raise ValueError("boom")

    outcome = security_gates.run_gate("crash", crash)

    assert outcome["status"] == "INCONCLUSIVE"
    assert "boom" in outcome["detail"]


def test_blocked_requires_the_action_to_be_denied():
    def denied():
        raise PermissionError

    assert security_gates.blocked(denied) == "PASS"
    assert security_gates.blocked(lambda: None) == "FAIL"


def test_a_missing_canary_is_inconclusive_not_blocked(tmp_path):
    status, _ = security_gates.fake_home_secrets(tmp_path / "missing")

    assert status == "INCONCLUSIVE"


def test_a_missing_agent_socket_is_inconclusive_not_blocked(tmp_path):
    status, _ = security_gates.fake_ssh_agent(str(tmp_path / "missing.sock"))

    assert status == "INCONCLUSIVE"


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        (["PASS", "PASS"], "PASS"),
        (["PASS", "INCONCLUSIVE"], "INCONCLUSIVE"),
        (["INCONCLUSIVE", "FAIL"], "FAIL"),
    ],
)
def test_combine_keeps_the_worst_outcome(outcomes, expected):
    assert security_gates.combine(outcomes) == expected


def test_fake_secrets_that_can_be_read_fail_the_gate(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / "fake_secret").write_text("FAKE")
    (tmp_path / ".ssh" / "id_canary").write_text("FAKE")

    status, _ = security_gates.fake_home_secrets(tmp_path)

    assert status == "FAIL"


def _keychain_returns(monkeypatch, status):
    security = type(
        "Security", (), {"SecKeychainFindGenericPassword": lambda *_: status}
    )
    monkeypatch.setattr(security_gates.ctypes, "CDLL", lambda _: security)


def test_a_returned_keychain_item_fails_the_gate(monkeypatch):
    monkeypatch.setenv("VIGILANT_CANARY_KEYCHAIN_PRESENT", "confirmed")
    _keychain_returns(monkeypatch, 0)

    assert security_gates.fake_keychain_item()[0] == "FAIL"


def test_keychain_not_found_passes_only_when_the_item_was_confirmed(monkeypatch):
    _keychain_returns(monkeypatch, security_gates.KEYCHAIN_NOT_FOUND)

    monkeypatch.delenv("VIGILANT_CANARY_KEYCHAIN_PRESENT", raising=False)
    assert security_gates.fake_keychain_item()[0] == "INCONCLUSIVE"

    monkeypatch.setenv("VIGILANT_CANARY_KEYCHAIN_PRESENT", "confirmed")
    assert security_gates.fake_keychain_item()[0] == "PASS"


def test_unexpected_keychain_status_is_inconclusive(monkeypatch):
    _keychain_returns(monkeypatch, -128)

    assert security_gates.fake_keychain_item()[0] == "INCONCLUSIVE"


def test_gate_workflow_cannot_come_from_a_pull_request():
    triggers = WORKFLOW.split("on:\n")[1].split("permissions:")[0]

    assert re.findall(r"^  (\w+):", triggers, re.MULTILINE) == ["workflow_dispatch"]
    assert "pull_request" not in WORKFLOW
    assert "contents: read" in WORKFLOW
    assert all(
        re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action)
        for action in re.findall(r"uses: (\S+)", WORKFLOW)
    )


def test_profile_is_parameterized_and_has_no_personal_paths():
    assert '(param "HOME_DIR")' in PROFILE
    assert '(param "RUNNER_DIR")' in PROFILE
    assert not re.search(r"/Users/(?!Shared)[a-z]", PROFILE)


def test_profile_limits_job_writes_to_the_session_folder():
    assert (
        '(allow file-write* (subpath (string-append (param "RUNNER_DIR") "/session")))'
        in PROFILE
    )
    assert (
        '(allow file-read* file-write* (subpath (param "RUNNER_DIR")))' not in PROFILE
    )
    deny = PROFILE.index('(deny file-write* (subpath (param "RUNNER_DIR")))')
    assert deny < PROFILE.index('"/session")))')


def test_profile_does_not_offer_the_signal_rule_that_failed_testing():
    active = [line for line in PROFILE.splitlines() if not line.startswith(";;")]

    assert not any("deny signal" in line for line in active)


def test_profile_allows_only_name_lookups_through_unix_sockets():
    assert "(deny network-outbound (remote unix-socket))" in PROFILE
    assert '(path-literal "/private/var/run/mDNSResponder")' in PROFILE


def test_documentation_states_the_required_warnings():
    text = " ".join(README.split())

    assert "deprecated by Apple and is not a hardened security boundary" in text
    assert "Conditional, supervised demo only" in text
    assert "Safer option" in text
    assert "A restricted working directory is **not** isolation" in text
    assert "Run the whole runner inside the sandbox" in text
    assert "--ephemeral" in text
    assert "failed or inconclusive mandatory gate means NO-GO" in text
