import http.client
import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_MODEL_DIR = Path.home() / ".cache" / "vigilant" / "models"
LOCAL_HOST = "127.0.0.1"
SHUTDOWN_SECONDS = 5


class ModelExecutionError(RuntimeError):
    """A local model could not load or run. No other detection method replaces it."""


class ContextExceededError(ModelExecutionError):
    """The input does not fit in the model's configured context."""


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
        try:
            process.wait(timeout=SHUTDOWN_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def post_json(url: str, payload: dict, timeout: float = 600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        if "exceed_context_size" in body:
            raise ContextExceededError(body) from error
        raise ModelExecutionError(
            f"inference request failed: {error}: {body}"
        ) from error
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise ModelExecutionError(f"inference request failed: {error}") from error


def llama_embeddings(url: str, texts: list[str]) -> list[list[float]]:
    response = post_json(f"{url}/v1/embeddings", {"input": texts})
    try:
        items = sorted(response["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]
    except (KeyError, TypeError) as error:
        raise ModelExecutionError(f"malformed embedding response: {error}") from error


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
