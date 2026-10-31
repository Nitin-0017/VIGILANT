import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "vigilant" / "models"
LOCAL_HOST = "127.0.0.1"
PEAK_FOOTPRINT = re.compile(r"phys_footprint_peak:\s+(\d+)")


class ModelExecutionError(RuntimeError):
    """A local model could not load or run. No other detection method replaces it."""


@contextmanager
def llama_server(
    model: Path,
    extra_args: list[str],
    executable: str = "llama-server",
    startup_timeout: float = 600,
) -> Iterator[dict]:
    """Run llama-server on a free local port until the block ends."""
    if shutil.which(executable) is None:
        raise ModelExecutionError(f"{executable} not found")
    if not model.is_file():
        raise ModelExecutionError(f"model file not found: {model}")

    port = _free_port()
    command = [
        executable,
        "--model",
        str(model),
        "--host",
        LOCAL_HOST,
        "--port",
        str(port),
        *extra_args,
    ]
    started = time.perf_counter()
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    url = f"http://{LOCAL_HOST}:{port}"
    try:
        _wait_until_healthy(url, process, startup_timeout)
        yield {
            "url": url,
            "pid": process.pid,
            "load_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        process.terminate()
        process.wait(timeout=30)


def post_json(url: str, payload: dict, timeout: float = 600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as error:
        raise ModelExecutionError(f"inference request failed: {error}") from error


def llama_embeddings(url: str, texts: list[str]) -> list[list[float]]:
    response = post_json(f"{url}/v1/embeddings", {"input": texts})
    try:
        items = sorted(response["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]
    except (KeyError, TypeError) as error:
        raise ModelExecutionError(f"malformed embedding response: {error}") from error


@contextmanager
def resource_samples(pid: int, interval: float = 0.5) -> Iterator[dict]:
    """Record the peak memory and mean CPU of a process while the block runs."""
    usage = {"peak_rss_mb": 0.0, "peak_footprint_mb": None, "mean_cpu_percent": None}
    cpu_samples = []
    finished = threading.Event()

    def sample() -> None:
        while not finished.wait(interval):
            rss_kb, cpu = _process_stats(pid)
            usage["peak_rss_mb"] = max(usage["peak_rss_mb"], round(rss_kb / 1024, 1))
            cpu_samples.append(cpu)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        yield usage
    finally:
        usage["peak_footprint_mb"] = _peak_footprint_mb(pid)
        finished.set()
        sampler.join()
        if cpu_samples:
            usage["mean_cpu_percent"] = round(sum(cpu_samples) / len(cpu_samples), 1)


def _process_stats(pid: int) -> tuple[float, float]:
    output = subprocess.run(
        ["ps", "-o", "rss=,%cpu=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.split()
    return (float(output[0]), float(output[1])) if len(output) == 2 else (0.0, 0.0)


def _peak_footprint_mb(pid: int) -> float | None:
    """Read macOS's peak footprint, which includes GPU memory that RSS can miss."""
    if shutil.which("footprint") is None:
        return None
    output = subprocess.run(
        ["footprint", "--format", "bytes", "--noCategories", str(pid)],
        capture_output=True,
        text=True,
    ).stdout
    peak = PEAK_FOOTPRINT.search(output)
    return round(int(peak[1]) / 1024**2, 1) if peak else None


def _free_port() -> int:
    with socket.socket() as server:
        server.bind((LOCAL_HOST, 0))
        return server.getsockname()[1]


def _wait_until_healthy(url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ModelExecutionError(
                f"llama-server exited with code {process.returncode}"
            )
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise ModelExecutionError(f"llama-server was not ready after {timeout} seconds")
