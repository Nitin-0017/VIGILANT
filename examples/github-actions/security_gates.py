"""Check a sandboxed runner before it scans any pull request. Read README.md first.

Probes use fake canaries only. A failed or inconclusive gate means NO-GO. Passing means
these specific probes were blocked, not that the sandbox is secure or that VIGILANT is
accurate.
"""

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
ASSESSED = "ASSESSED"
CANARY_SERVICE = b"vigilant-canary"
KEYCHAIN_NOT_FOUND = -25300
# Status codes that mean the keychain service could not be used at all. Any other code may
# mean the item was reached, such as a declined prompt, so it does not count as a pass.
KEYCHAIN_REFUSED = {-50, -25307}
CODER_MODEL = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
EMBEDDING_MODEL = "Qwen3-Embedding-0.6B-Q8_0.gguf"


def blocked(action) -> str:
    """Return PASS only for a permission denial, so a missing canary is never a pass."""
    try:
        action()
    except PermissionError:
        return PASS
    except OSError:
        return INCONCLUSIVE
    return FAIL


def combine(outcomes) -> str:
    outcomes = set(outcomes)
    if FAIL in outcomes:
        return FAIL
    return INCONCLUSIVE if INCONCLUSIVE in outcomes else PASS


def is_sandboxed() -> tuple[str, str]:
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    state = libc.sandbox_check(os.getpid(), None, 0)
    return (PASS, "process is sandboxed") if state else (FAIL, "not sandboxed")


def fake_home_secrets(canary_dir: Path) -> tuple[str, str]:
    canaries = [canary_dir / "fake_secret", canary_dir / ".ssh" / "id_canary"]
    outcomes = [blocked(lambda p=path: p.read_text()) for path in canaries]
    outcomes.append(blocked(lambda: os.listdir(canary_dir)))
    return combine(outcomes), f"read and list attempts: {outcomes}"


def fake_ssh_agent(path: str) -> tuple[str, str]:
    def connect():
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(path)

    return blocked(connect), f"connection to the fake agent socket {path}"


def fake_keychain_item() -> tuple[str, str]:
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    size = ctypes.c_uint32()
    data = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(CANARY_SERVICE),
        CANARY_SERVICE,
        len(CANARY_SERVICE),
        CANARY_SERVICE,
        ctypes.byref(size),
        ctypes.byref(data),
        None,
    )
    if status == 0:
        return FAIL, "the fake keychain item was returned"
    if status == KEYCHAIN_NOT_FOUND:
        # Only a check outside the sandbox can show that this means hidden, not missing.
        if os.environ.get("VIGILANT_CANARY_KEYCHAIN_PRESENT") == "confirmed":
            return PASS, "fake item exists outside the sandbox but was not found inside"
        return (
            INCONCLUSIVE,
            "not found, and nothing confirmed that the fake item exists",
        )
    if status in KEYCHAIN_REFUSED:
        return PASS, f"keychain lookup refused with status {status}"
    return INCONCLUSIVE, f"unexpected keychain status {status}"


def clipboard() -> tuple[str, str]:
    tool = blocked(_run_pbpaste)
    if tool != PASS:
        return tool, "pbpaste was not denied"
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    send = objc.objc_msgSend
    send.restype = ctypes.c_void_p
    send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    board = send(
        objc.objc_getClass(b"NSPasteboard"),
        objc.sel_registerName(b"generalPasteboard"),
    )
    send.restype = ctypes.c_long
    # The operator copies text first, so a reachable clipboard has a positive count.
    count = send(board, objc.sel_registerName(b"changeCount"))
    return (
        (FAIL, "clipboard is reachable") if count > 0 else (PASS, "clipboard refused")
    )


def _run_pbpaste() -> None:
    subprocess.run(["/usr/bin/pbpaste"], capture_output=True, timeout=5)


def screen_capture() -> tuple[str, str]:
    outcome = blocked(
        lambda: subprocess.run(["/usr/sbin/screencapture", "-h"], capture_output=True)
    )
    if outcome != PASS:
        return outcome, "screencapture was not denied"
    graphics = ctypes.CDLL(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    if graphics.CGPreflightScreenCaptureAccess():
        return INCONCLUSIVE, "screen recording permission is present for this process"
    return PASS, "screencapture blocked and no screen recording permission"


def launch_agent_and_homebrew(home: Path) -> tuple[str, str]:
    agent = home / "Library" / "LaunchAgents" / "vigilant-canary.plist"
    brew = Path("/opt/homebrew/.vigilant-canary")
    outcomes = {
        "launch agent file": blocked(lambda: agent.write_text("")),
        "homebrew file": blocked(lambda: brew.write_text("")),
        "launchctl": blocked(
            lambda: subprocess.run(["/bin/launchctl", "help"], capture_output=True)
        ),
    }
    status = combine(outcomes.values())
    if status != PASS:
        agent.unlink(missing_ok=True)
        brew.unlink(missing_ok=True)
    return status, json.dumps(outcomes)


def network_exposure(local_port: int) -> tuple[str, str]:
    """Record what stays reachable. These are risks the owner must accept, not passes."""

    def reachable(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), 5):
                return True
        except OSError:
            return False

    return ASSESSED, (
        f"internet reachable: {reachable('github.com', 443)}; "
        f"fake localhost service reachable: {reachable('127.0.0.1', local_port)}"
    )


def gpu_inference(model_dir: Path, work_dir: Path) -> tuple[str, str]:
    """Load both models as the engine does, run them, and read the layer placement."""
    coder = _gpu_check(
        model_dir / CODER_MODEL,
        ["--ctx-size", "2048", "--parallel", "1"],
        "/v1/chat/completions",
        {
            "messages": [{"role": "user", "content": "Count to ten in words."}],
            "temperature": 0,
            "seed": 42,
            "max_tokens": 40,
        },
        work_dir / "gpu-gate-coder.log",
    )
    embedding = _gpu_check(
        model_dir / EMBEDDING_MODEL,
        ["--embedding", "--pooling", "last", "--ctx-size", "2048", "--parallel", "1"],
        "/v1/embeddings",
        {"input": ["Count to ten in words."]},
        work_dir / "gpu-gate-embedding.log",
    )
    detail = f"coder: {coder[1]}; embedding: {embedding[1]}"
    return combine([coder[0], embedding[0]]), detail


def _gpu_check(
    model: Path, extra: list[str], path: str, body: dict, log: Path
) -> tuple[str, str]:
    port = _free_port()
    command = ["llama-server", "--model", str(model), "--host", "127.0.0.1"]
    command += ["--port", str(port), "--n-gpu-layers", "99", "--verbose", *extra]
    with log.open("w") as output:
        server = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
    try:
        url = f"http://127.0.0.1:{port}"
        if not _wait_for_health(url, server):
            return INCONCLUSIVE, "server did not become ready"
        request = urllib.request.Request(
            url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            response.read()
    finally:
        server.terminate()
        server.wait(timeout=30)
    text = log.read_text(errors="replace")
    on_gpu = text.count("assigned to device MTL0")
    on_cpu = text.count("assigned to device CPU")
    detail = f"layers on MTL0 {on_gpu}, on CPU {on_cpu}"
    if on_gpu and not on_cpu:
        return PASS, detail
    return (FAIL if on_cpu else INCONCLUSIVE), detail


def cancellation(vigilant: str, repo: Path, work_dir: Path) -> tuple[str, str]:
    """Cancel a real scan of the last commit and require its model server to stop.

    The sandbox hides other processes from pgrep, so a small script records the server's
    process ID and then becomes the real llama-server.
    """
    report = work_dir / "gate-cancel-report.json"
    pid_file = work_dir / "gate-server.pid"
    pid_file.unlink(missing_ok=True)
    server_path = work_dir / "gate-llama-server"
    server_path.write_text(
        f'#!/bin/sh\necho $$ > "{pid_file}"\nexec llama-server "$@"\n'
    )
    server_path.chmod(0o700)
    scan = subprocess.Popen(
        [
            vigilant,
            "scan",
            "HEAD~1",
            "--llama-server",
            str(server_path),
            "--report-file",
            str(report),
        ],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server = _wait_for_pid(pid_file, scan)
    if server is None:
        scan.kill()
        scan.wait()
        return (
            INCONCLUSIVE,
            "no model server started, so cancellation was not exercised",
        )
    scan.send_signal(signal.SIGTERM)
    code = scan.wait(timeout=30)
    left = _process_exists(server)
    status = json.loads(report.read_text()).get("status") if report.exists() else None
    detail = f"exit {code}; report {status}; server left running: {left}"
    if left:
        os.kill(server, signal.SIGKILL)
        return FAIL, detail
    return (PASS if status == "cancelled" else INCONCLUSIVE), detail


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for_health(url: str, server: subprocess.Popen, seconds: int = 300) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and server.poll() is None:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def _wait_for_pid(
    pid_file: Path, scan: subprocess.Popen, seconds: int = 120
) -> int | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and scan.poll() is None:
        if pid_file.exists() and pid_file.read_text().strip():
            time.sleep(2)
            return int(pid_file.read_text())
        time.sleep(0.1)
    return None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def decide(results: list[dict]) -> str:
    """Any failed or inconclusive gate means NO-GO."""
    statuses = {result["status"] for result in results}
    return "NO-GO" if statuses & {FAIL, INCONCLUSIVE} else "GATES PASSED"


def run_gate(name: str, check) -> dict:
    try:
        status, detail = check()
    except Exception as error:
        status, detail = INCONCLUSIVE, f"{type(error).__name__}: {error}"
    return {"gate": name, "status": status, "detail": detail}


def main() -> int:
    canary_dir = Path(os.environ["VIGILANT_CANARY_DIR"])
    model_dir = Path(os.environ["VIGILANT_MODEL_DIR"])
    home = Path(os.environ["VIGILANT_HOME_DIR"])
    work_dir = Path(os.environ["VIGILANT_GATE_WORK_DIR"])
    socket_path = os.environ["VIGILANT_CANARY_SOCKET"]
    local_port = int(os.environ["VIGILANT_CANARY_PORT"])
    vigilant = os.environ["VIGILANT_BIN"]

    gates = [
        ("1 Runs inside the sandbox", is_sandboxed),
        ("2 Fake home secrets", lambda: fake_home_secrets(canary_dir)),
        ("3a Fake SSH agent", lambda: fake_ssh_agent(socket_path)),
        ("3b Fake keychain item", fake_keychain_item),
        ("4a Clipboard", clipboard),
        ("4b Screen capture", screen_capture),
        ("5 Launch agent and Homebrew", lambda: launch_agent_and_homebrew(home)),
        ("6 Network and localhost", lambda: network_exposure(local_port)),
        ("7 GPU inference", lambda: gpu_inference(model_dir, work_dir)),
        ("8 Cancellation", lambda: cancellation(vigilant, Path.cwd(), work_dir)),
    ]
    results = [run_gate(name, check) for name, check in gates]
    decision = decide(results)

    lines = ["## VIGILANT runner security gates", "", "| Gate | Status | Detail |"]
    lines.append("| --- | --- | --- |")
    lines += [f"| {r['gate']} | {r['status']} | {r['detail']} |" for r in results]
    lines += [
        "",
        f"**Decision: {decision}.** This does not measure detection accuracy.",
    ]
    print("\n".join(lines))
    return 0 if decision == "GATES PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
