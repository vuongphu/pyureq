"""
GIL behaviour: rupy vs curl_cffi vs requests vs httpx
======================================================

Methodology
-----------
A background thread runs a tight CPU loop (incrementing a counter) for the
entire duration of the test.  Meanwhile the main thread makes N sequential
HTTP requests with a given library.

  • If the library HOLDS the GIL during I/O → the CPU thread is blocked for
    every request; it completes very little work.
  • If the library RELEASES the GIL during I/O → the CPU thread runs freely
    between recvfrom/sendto; it completes roughly as much work as it would
    with no HTTP traffic at all.

"GIL efficiency" = cpu_ticks_during_requests / cpu_ticks_baseline
  1.00 = perfect (GIL fully released, CPU ran unobstructed)
  0.00 = terrible (GIL held the whole time, CPU never ran)

Usage
-----
    python benchmarks/bench_gil.py
    python benchmarks/bench_gil.py --iterations 200
"""

import argparse
import threading
import time
import sys

sys.path.insert(0, "tests")
from local_server import start_server

PORT = 19998
BASE = f"http://127.0.0.1:{PORT}"
URL  = f"{BASE}/get"

# ---------------------------------------------------------------------------
# Library imports
# ---------------------------------------------------------------------------

LIBS: dict = {}

import rupy
LIBS["rupy"] = rupy

try:
    import requests
    LIBS["requests"] = requests
except ImportError:
    pass

try:
    import httpx
    LIBS["httpx"] = httpx
except ImportError:
    pass

try:
    import curl_cffi.requests as curl_cffi
    LIBS["curl_cffi"] = curl_cffi
except ImportError:
    pass


# ---------------------------------------------------------------------------
# CPU worker
# ---------------------------------------------------------------------------

class CpuWorker(threading.Thread):
    """Spins counting integers until .stop() is called."""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop_flag = threading.Event()
        self.count = 0

    def run(self):
        c = 0
        while not self._stop_flag.is_set():
            c += 1
        self.count = c

    def stop(self):
        self._stop_flag.set()
        self.join()


def measure_baseline_ticks(duration_secs: float = 0.5) -> float:
    """How many ticks can the CPU worker do when nothing else is competing?"""
    w = CpuWorker()
    w.start()
    time.sleep(duration_secs)
    w.stop()
    return w.count / duration_secs   # ticks per second


# ---------------------------------------------------------------------------
# Per-library test
# ---------------------------------------------------------------------------

def run_gil_test(lib, n: int) -> dict:
    """
    Make n requests with `lib` while a CPU thread counts.
    Returns ticks/sec during the run and the mean request latency.
    """
    # Warmup
    for _ in range(5):
        try:
            lib.get(URL)
        except Exception:
            pass

    cpu = CpuWorker()
    cpu.start()

    times = []
    t_start = time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            lib.get(URL)
        except Exception as e:
            print(f"    request error: {e}", file=sys.stderr)
        times.append(time.perf_counter() - t0)
    t_total = time.perf_counter() - t_start

    cpu.stop()

    tps = cpu.count / t_total   # ticks/sec during I/O
    mean_ms = (sum(times) / len(times)) * 1000 if times else float("nan")
    return {"ticks_per_sec": tps, "mean_req_ms": mean_ms}


# ---------------------------------------------------------------------------
# Session variant
# ---------------------------------------------------------------------------

def run_session_gil_test(lib_name: str, lib, n: int) -> dict:
    """Same test but using a persistent session / Client."""
    if lib_name == "rupy":
        sess = lib.Session()
    elif lib_name == "requests":
        sess = lib.Session()
    elif lib_name == "httpx":
        sess = lib.Client()
    elif lib_name == "curl_cffi":
        sess = lib.Session()
    else:
        return {}

    # Warmup
    for _ in range(5):
        try:
            sess.get(URL)
        except Exception:
            pass

    cpu = CpuWorker()
    cpu.start()

    times = []
    t_start = time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            sess.get(URL)
        except Exception as e:
            print(f"    request error: {e}", file=sys.stderr)
        times.append(time.perf_counter() - t0)
    t_total = time.perf_counter() - t_start

    cpu.stop()

    try:
        sess.close()
    except Exception:
        pass

    tps = cpu.count / t_total
    mean_ms = (sum(times) / len(times)) * 1000 if times else float("nan")
    return {"ticks_per_sec": tps, "mean_req_ms": mean_ms}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

W = 80

def _bar(ratio: float, width: int = 30) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def _verdict(ratio: float) -> str:
    if ratio >= 0.80:
        return "✓ GIL released"
    if ratio >= 0.40:
        return "~ partial"
    return "✗ GIL held"


def print_results(label: str, results: dict, baseline_tps: float):
    print()
    print(f"  {label}")
    print(f"  {'─' * (W - 2)}")
    print(f"  {'Library':<12}  {'GIL efficiency':^32}  {'req ms':>7}  {'verdict'}")
    print(f"  {'─' * (W - 2)}")

    for lib_name, r in results.items():
        if not r:
            continue
        tps = r["ticks_per_sec"]
        ratio = min(tps / baseline_tps, 1.0)
        bar = _bar(ratio)
        pct = f"{ratio*100:5.1f}%"
        mean_ms = r["mean_req_ms"]
        verdict = _verdict(ratio)
        print(f"  {lib_name:<12}  {bar} {pct}  {mean_ms:>7.2f}  {verdict}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GIL behaviour benchmark")
    parser.add_argument("--iterations", "-n", type=int, default=150,
                        help="HTTP requests per library (default: 150)")
    args = parser.parse_args()

    print(f"\nStarting local server on port {PORT}...")
    start_server(PORT)

    print("Measuring CPU baseline (no HTTP traffic)…", end=" ", flush=True)
    baseline_tps = measure_baseline_ticks(duration_secs=1.0)
    print(f"{baseline_tps/1e6:.1f}M ticks/sec\n")

    print("=" * W)
    print("  GIL BENCHMARK — CPU work completed while making HTTP requests")
    print(f"  Baseline CPU throughput: {baseline_tps/1e6:.1f}M ticks/sec")
    print(f"  100% = GIL fully released (CPU ran unobstructed)")
    print(f"    0% = GIL held          (CPU completely blocked)")
    print("=" * W)

    # Stateless
    stateless_results = {}
    for lib_name, lib in LIBS.items():
        print(f"  [{lib_name:12}] stateless …", end=" ", flush=True)
        r = run_gil_test(lib, args.iterations)
        stateless_results[lib_name] = r
        pct = r["ticks_per_sec"] / baseline_tps * 100
        print(f"{pct:.1f}%")

    print_results("STATELESS (new connection per request)", stateless_results, baseline_tps)

    # Session
    print()
    session_results = {}
    for lib_name, lib in LIBS.items():
        print(f"  [{lib_name:12}] session   …", end=" ", flush=True)
        r = run_session_gil_test(lib_name, lib, args.iterations)
        session_results[lib_name] = r
        if r:
            pct = r["ticks_per_sec"] / baseline_tps * 100
            print(f"{pct:.1f}%")
        else:
            print("N/A")

    print_results("SESSION (persistent connection pool)", session_results, baseline_tps)

    # Explanation
    print()
    print("=" * W)
    print("  EXPLANATION")
    print("  ─" + "─" * (W - 3))
    print("  GIL efficiency measures how much CPU work a background thread")
    print("  completed while the main thread was blocked in HTTP I/O.")
    print()
    print("  ✓ GIL released  — library yields the GIL during network calls;")
    print("    Python threads (asyncio, data processing) run concurrently.")
    print()
    print("  ✗ GIL held      — library blocks the interpreter; all other")
    print("    Python threads are frozen for the duration of each request.")
    print("=" * W)
    print()


if __name__ == "__main__":
    main()
