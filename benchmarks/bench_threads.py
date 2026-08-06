"""
Concurrent threads benchmark: pyureq vs curl_cffi (and others)
=============================================================

Spawns N threads simultaneously, each making M HTTP requests.
Measures: total throughput, per-request latency, error rate, memory overhead.

Key questions answered
----------------------
  1. Thread-safety — does the library crash or corrupt data under load?
  2. Throughput — requests/sec as thread count grows
  3. Latency under contention — how does p99 degrade?
  4. Memory — RSS growth per additional thread

Usage
-----
    python benchmarks/bench_threads.py
    python benchmarks/bench_threads.py --max-threads 500 --requests 10
    python benchmarks/bench_threads.py --threads 1000 --requests 5 --no-session
"""

import argparse
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "tests")

PORT = 19997
BASE = f"http://127.0.0.1:{PORT}"
URL  = f"{BASE}/get"

# ---------------------------------------------------------------------------
# Multi-threaded server (Werkzeug threaded mode so it handles concurrency)
# ---------------------------------------------------------------------------

def start_threaded_server():
    from flask import Flask, jsonify, request as freq
    from werkzeug.serving import WSGIRequestHandler
    app = Flask(__name__)

    # HTTP/1.1 so connections are reusable.  Under Werkzeug's HTTP/1.0 default
    # the socket closes after every response, which makes the SESSION sweep
    # below a duplicate of STATELESS and burns one ephemeral port per request.
    WSGIRequestHandler.protocol_version = "HTTP/1.1"

    @app.route("/get")
    def get_route():
        return jsonify({"args": dict(freq.args), "url": freq.url}), 200

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT,
                               threaded=True, use_reloader=False),
        daemon=True,
    )
    t.start()

    import socket
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    print(f"  Threaded server ready on port {PORT}")


# ---------------------------------------------------------------------------
# Library imports
# ---------------------------------------------------------------------------

LIBS: dict = {}

import pyureq
LIBS["pyureq"] = pyureq

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
# Memory helper
# ---------------------------------------------------------------------------

def rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------

TIMEOUT = 10  # per-request timeout in seconds

def stateless_worker(lib, n: int) -> tuple[list[float], int]:
    """n sequential requests, returns (latencies_ms, error_count)."""
    times, errors = [], 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            r = lib.get(URL, timeout=TIMEOUT)
            if r.status_code != 200:
                errors += 1
        except Exception:
            errors += 1
        times.append((time.perf_counter() - t0) * 1000)
    return times, errors


def session_worker(session, n: int) -> tuple[list[float], int]:
    times, errors = [], 0
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            r = session.get(URL, timeout=TIMEOUT)
            if r.status_code != 200:
                errors += 1
        except Exception:
            errors += 1
        times.append((time.perf_counter() - t0) * 1000)
    return times, errors


# ---------------------------------------------------------------------------
# Run one scenario (stateless or session) for a given thread count
# ---------------------------------------------------------------------------

def run_stateless(lib, n_threads: int, n_req: int) -> dict:
    mem_before = rss_mb()

    # Warmup (single thread)
    for _ in range(3):
        try: lib.get(URL)
        except Exception: pass

    all_times, total_errors = [], 0
    t_wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(stateless_worker, lib, n_req) for _ in range(n_threads)]
        for f in as_completed(futs):
            times, errs = f.result()
            all_times.extend(times)
            total_errors += errs

    t_wall = time.perf_counter() - t_wall_start
    mem_after = rss_mb()

    return _summarise(all_times, total_errors, t_wall, mem_after - mem_before)


def run_session(lib_name: str, lib, n_threads: int, n_req: int) -> dict:
    # Per-thread sessions (each thread owns a session — safe for all libs)
    mem_before = rss_mb()

    def _make_session():
        if lib_name == "pyureq":
            return lib.Session()
        elif lib_name == "requests":
            return lib.Session()
        elif lib_name == "httpx":
            return lib.Client()
        elif lib_name == "curl_cffi":
            return lib.Session()
        return None

    # Warmup
    s = _make_session()
    for _ in range(3):
        try: s.get(URL)
        except Exception: pass
    try: s.close()
    except Exception: pass

    all_times, total_errors = [], 0
    t_wall_start = time.perf_counter()

    def _worker(n):
        sess = _make_session()
        times, errors = session_worker(sess, n)
        try: sess.close()
        except Exception: pass
        return times, errors

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(_worker, n_req) for _ in range(n_threads)]
        for f in as_completed(futs):
            times, errs = f.result()
            all_times.extend(times)
            total_errors += errs

    t_wall = time.perf_counter() - t_wall_start
    mem_after = rss_mb()

    return _summarise(all_times, total_errors, t_wall, mem_after - mem_before)


def _summarise(times, errors, wall, mem_delta) -> dict:
    if not times:
        return {}
    s = sorted(times)
    n = len(s)
    return {
        "total":    n,
        "errors":   errors,
        "wall_s":   wall,
        "rps":      n / wall,
        "mean_ms":  statistics.mean(times),
        "p50_ms":   s[n // 2],
        "p95_ms":   s[int(n * 0.95)],
        "p99_ms":   s[int(n * 0.99)],
        "mem_mb":   mem_delta,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

COLS = ["threads", "rps", "mean_ms", "p95_ms", "p99_ms", "errors", "mem_mb"]

def _hdr():
    print(f"  {'threads':>8}  {'req/s':>8}  {'mean ms':>8}  "
          f"{'p95 ms':>8}  {'p99 ms':>8}  {'errors':>7}  {'ΔRSS MB':>8}")
    print("  " + "─" * 72)

def _row(n_threads, r):
    err_s = f"{r['errors']}/{r['total']}"
    print(f"  {n_threads:>8}  {r['rps']:>8.1f}  {r['mean_ms']:>8.2f}  "
          f"  {r['p95_ms']:>8.2f}  {r['p99_ms']:>8.2f}  {err_s:>7}  {r['mem_mb']:>8.1f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Thread concurrency benchmark")
    parser.add_argument("--requests",    "-r", type=int, default=10,
                        help="Requests per thread (default: 10)")
    parser.add_argument("--max-threads", "-t", type=int, default=1000,
                        help="Maximum thread count (default: 1000)")
    parser.add_argument("--threads",           type=int, default=None,
                        help="Run a single thread count instead of a sweep")
    parser.add_argument("--no-session",        action="store_true",
                        help="Skip session mode")
    args = parser.parse_args()

    # Thread counts to sweep (powers of ~2 up to max)
    if args.threads:
        thread_counts = [args.threads]
    else:
        thread_counts = [1, 10, 50, 100, 200, 500, 1000]
        thread_counts = [t for t in thread_counts if t <= args.max_threads]

    print("\nStarting multi-threaded server...")
    start_threaded_server()
    print(f"Libraries: {', '.join(LIBS)}")
    print(f"Requests per thread: {args.requests}")
    print()

    # ── STATELESS ────────────────────────────────────────────────────────────
    print("═" * 80)
    print("  STATELESS — each thread makes fresh connections")
    print("  (httpx skipped: httpx.get() creates/destroys an event loop per")
    print("   call and is not designed for stateless high-concurrency use)")
    print("═" * 80)

    for lib_name, lib in LIBS.items():
        if lib_name == "httpx":
            continue   # httpx stateless is broken by design at high concurrency
        print(f"\n  [{lib_name}]")
        _hdr()
        for n_threads in thread_counts:
            r = run_stateless(lib, n_threads, args.requests)
            if r:
                _row(n_threads, r)

    if args.no_session:
        return

    # ── SESSION (per-thread) ─────────────────────────────────────────────────
    print()
    print("═" * 80)
    print("  SESSION — each thread owns a persistent session/Client")
    print("═" * 80)

    for lib_name, lib in LIBS.items():
        print(f"\n  [{lib_name}]")
        _hdr()
        for n_threads in thread_counts:
            r = run_session(lib_name, lib, n_threads, args.requests)
            if r:
                _row(n_threads, r)

    # ── SUMMARY at max threads ───────────────────────────────────────────────
    max_t = thread_counts[-1]
    total_req = max_t * args.requests
    print()
    print("═" * 80)
    print(f"  SUMMARY at {max_t} threads × {args.requests} req = {total_req} total requests")
    print("═" * 80)
    print(f"  {'Library':<12}  {'Mode':<12}  {'req/s':>8}  {'p99 ms':>8}  {'errors':>8}")
    print("  " + "─" * 56)

    for lib_name, lib in LIBS.items():
        rs = run_stateless(lib, max_t, args.requests)
        if rs:
            err = f"{rs['errors']}/{rs['total']}"
            print(f"  {lib_name:<12}  {'stateless':<12}  "
                  f"{rs['rps']:>8.1f}  {rs['p99_ms']:>8.2f}  {err:>8}")

    if not args.no_session:
        for lib_name, lib in LIBS.items():
            rs = run_session(lib_name, lib, max_t, args.requests)
            if rs:
                err = f"{rs['errors']}/{rs['total']}"
                print(f"  {lib_name:<12}  {'session':<12}  "
                      f"{rs['rps']:>8.1f}  {rs['p99_ms']:>8.2f}  {err:>8}")

    print()


if __name__ == "__main__":
    main()
