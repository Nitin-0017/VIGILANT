import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager

PEAK_FOOTPRINT = re.compile(r"phys_footprint_peak:\s+(\d+)")


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
