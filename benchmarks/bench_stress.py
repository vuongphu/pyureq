"""
Duration-based stress test: pyureq vs requests, httpx, curl_cffi
==============================================================

Unlike bench_threads.py (which fixes requests-per-thread so higher thread
counts finish faster), every concurrency level here runs for a fixed wall-clock
window.  That gives a fair, comparable req/s at every level and a clear picture
of where errors and latency start to spike.

Usage
-----
    python benchmarks/bench_stress.py
    python benchmarks/bench_stress.py --duration 10 --warmup 2 --threads 1,10,50,100,200
    python benchmarks/bench_stress.py --threads 50,100 --json results/stress.json
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "tests")
from local_server import start_server

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
# Memory helper (Linux /proc; safe no-op on Windows)
# ---------------------------------------------------------------------------

def _rss_mb() -> float:
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


def _stateless_worker(get_fn, url, duration_s):
    deadline = time.monotonic() + duration_s
    times_ms = []
    err_timeout = err_conn = err_http = err_other = 0
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            r = get_fn(url, timeout=TIMEOUT)
            elapsed = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                times_ms.append(elapsed)
            else:
                err_http += 1
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "timeout" in name:
                err_timeout += 1
            elif "connection" in name:
                err_conn += 1
            else:
                err_other += 1
    return times_ms, (err_timeout, err_conn, err_http, err_other)


def _session_worker(make_session_fn, url, duration_s):
    session = make_session_fn()
    deadline = time.monotonic() + duration_s
    times_ms = []
    err_timeout = err_conn = err_http = err_other = 0
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            r = session.get(url, timeout=TIMEOUT)
            elapsed = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                times_ms.append(elapsed)
            else:
                err_http += 1
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "timeout" in name:
                err_timeout += 1
            elif "connection" in name:
                err_conn += 1
            else:
                err_other += 1
    try:
        session.close()
    except Exception:
        pass
    return times_ms, (err_timeout, err_conn, err_http, err_other)


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------

def _summarise(n_threads, worker_results, wall_s, mem_delta) -> dict:
    all_times = sorted(t for times, _ in worker_results for t in times)
    all_errors = [e for _, e in worker_results]
    n_ok = len(all_times)
    errors = tuple(sum(e[i] for e in all_errors) for i in range(4))
    total = n_ok + sum(errors)

    def pct(p):
        if not n_ok:
            return 0.0
        return all_times[min(int(n_ok * p), n_ok - 1)]

    return {
        "threads":     n_threads,
        "total":       total,
        "ok":          n_ok,
        "err_pct":     100.0 * sum(errors) / total if total else 0.0,
        "err_timeout": errors[0],
        "err_conn":    errors[1],
        "err_http":    errors[2],
        "err_other":   errors[3],
        "wall_s":      wall_s,
        "rps":         total / wall_s if wall_s else 0.0,
        "mean_ms":     statistics.mean(all_times) if all_times else 0.0,
        "p95_ms":      pct(0.95),
        "p99_ms":      pct(0.99),
        "p999_ms":     pct(0.999),
        "mem_mb":      mem_delta,
    }


# ---------------------------------------------------------------------------
# Per-level runner
# ---------------------------------------------------------------------------

def _run_level_stateless(get_fn, n_threads, duration_s, warmup_s, url):
    # Warmup — results discarded
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(_stateless_worker, get_fn, url, warmup_s)
                for _ in range(n_threads)]
        for f in as_completed(futs):
            f.result()

    mem_before = _rss_mb()
    wall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(_stateless_worker, get_fn, url, duration_s)
                for _ in range(n_threads)]
        results = [f.result() for f in as_completed(futs)]
    wall_s = time.monotonic() - wall_start
    mem_delta = _rss_mb() - mem_before

    return _summarise(n_threads, results, wall_s, mem_delta)


def _run_level_session(make_session_fn, n_threads, duration_s, warmup_s, url):
    # Warmup
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(_session_worker, make_session_fn, url, warmup_s)
                for _ in range(n_threads)]
        for f in as_completed(futs):
            f.result()

    mem_before = _rss_mb()
    wall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = [ex.submit(_session_worker, make_session_fn, url, duration_s)
                for _ in range(n_threads)]
        results = [f.result() for f in as_completed(futs)]
    wall_s = time.monotonic() - wall_start
    mem_delta = _rss_mb() - mem_before

    return _summarise(n_threads, results, wall_s, mem_delta)


def _make_session_fn(lib_name, lib):
    def _make():
        if lib_name == "httpx":
            return lib.Client()
        return lib.Session()
    return _make


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

HDR = (f"  {'threads':>8}  {'req/s':>8}  {'mean ms':>8}  "
       f"{'p95 ms':>8}  {'p99 ms':>8}  {'p999 ms':>9}  "
       f"{'err%':>7}  {'t/o':>5}  {'ΔRSS MB':>8}")
SEP = "  " + "─" * 82


def _flag(err_pct, threshold):
    if err_pct >= threshold:
        return " ✗"
    if err_pct > threshold / 2:
        return " ⚠"
    return "  "


def _print_row(r, threshold):
    flag = _flag(r["err_pct"], threshold)
    print(
        f"  {r['threads']:>8}  {r['rps']:>8.1f}  {r['mean_ms']:>8.2f}  "
        f"{r['p95_ms']:>8.2f}  {r['p99_ms']:>8.2f}  {r['p999_ms']:>9.2f}  "
        f"{r['err_pct']:>6.2f}%{flag}  {r['err_timeout']:>5}  {r['mem_mb']:>8.1f}"
    )


def _print_table(lib_name, mode, rows, threshold):
    print(f"\n{lib_name} — {mode.upper()}")
    print(HDR)
    print(SEP)
    for r in rows:
        _print_row(r, threshold)
    print(SEP)

    if not rows:
        return

    peak = max(rows, key=lambda r: r["rps"])
    print(f"  Peak:  {peak['rps']:.1f} req/s @ {peak['threads']} threads"
          f"  (p99 = {peak['p99_ms']:.2f} ms  err = {peak['err_pct']:.2f}%)")

    breaks = [r for r in rows if r["err_pct"] >= threshold]
    if breaks:
        b = breaks[0]
        print(f"  Break: {b['threads']} threads — "
              f"err {b['err_pct']:.2f}% > {threshold:.2f}% threshold")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Duration-based stress benchmark")
    parser.add_argument("--duration",        type=float, default=5,
                        help="Seconds per thread level (default: 5)")
    parser.add_argument("--warmup",          type=float, default=2,
                        help="Warmup seconds per level (default: 2)")
    parser.add_argument("--threads",         type=str,   default="1,10,50,100,200",
                        help="Thread sweep: comma-separated (default: 1,10,50,100,200)")
    parser.add_argument("--port",            type=int,   default=19996,
                        help="Local server port (default: 19996)")
    parser.add_argument("--error-threshold", type=float, default=5.0,
                        help="Error %% to flag breaking point (default: 5.0)")
    parser.add_argument("--json",            type=str,   default=None,
                        help="Write JSON results to FILE")
    args = parser.parse_args()

    thread_counts = [int(t.strip()) for t in args.threads.split(",") if t.strip()]

    print("\nStarting local server...")
    start_server(args.port)
    url = f"http://127.0.0.1:{args.port}/get"
    print(f"  Server ready on port {args.port}")
    print(f"Libraries: {', '.join(LIBS)}")
    print(f"Duration: {args.duration}s / level   Warmup: {args.warmup}s / level")
    print(f"Thread sweep: {thread_counts}")
    print(f"Error threshold: {args.error_threshold}%")
    print()

    all_results: dict = {}  # lib_name → {mode: [row, ...]}

    # ── STATELESS ─────────────────────────────────────────────────────────────
    print("═" * 86)
    print("  STATELESS — each thread makes fresh connections")
    print("  (httpx skipped: event-loop lifecycle not suited for stateless concurrency)")
    print("═" * 86)

    for lib_name, lib in LIBS.items():
        if lib_name == "httpx":
            continue
        rows = []
        for n in thread_counts:
            r = _run_level_stateless(lib.get, n, args.duration, args.warmup, url)
            rows.append(r)
        _print_table(lib_name, "stateless", rows, args.error_threshold)
        all_results.setdefault(lib_name, {})["stateless"] = rows

    # ── SESSION (per-thread) ───────────────────────────────────────────────────
    print()
    print("═" * 86)
    print("  SESSION — each thread owns a persistent session/Client")
    print("═" * 86)

    for lib_name, lib in LIBS.items():
        make_fn = _make_session_fn(lib_name, lib)
        rows = []
        for n in thread_counts:
            r = _run_level_session(make_fn, n, args.duration, args.warmup, url)
            rows.append(r)
        _print_table(lib_name, "session", rows, args.error_threshold)
        all_results.setdefault(lib_name, {})["session"] = rows

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    print()
    print("═" * 70)
    print("STRESS TEST SUMMARY")
    print(f"  {'Library':<14}  {'Mode':<10}  {'peak req/s':>10}  "
          f"{'@ threads':>9}  {'p99 ms':>8}  {'max err%':>8}")
    print("  " + "─" * 64)

    for lib_name, modes in all_results.items():
        for mode, rows in modes.items():
            if not rows:
                continue
            peak = max(rows, key=lambda r: r["rps"])
            max_err = max(r["err_pct"] for r in rows)
            print(f"  {lib_name:<14}  {mode:<10}  {peak['rps']:>10.1f}  "
                  f"{peak['threads']:>9}  {peak['p99_ms']:>8.2f}  {max_err:>7.2f}%")

    print("═" * 70)
    print()

    # ── JSON output ───────────────────────────────────────────────────────────
    if args.json:
        payload = {
            "config": {
                "duration_s":    args.duration,
                "warmup_s":      args.warmup,
                "thread_levels": thread_counts,
                "port":          args.port,
                "error_threshold": args.error_threshold,
            },
            "results": all_results,
        }
        os.makedirs(os.path.dirname(args.json) if os.path.dirname(args.json) else ".", exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"JSON results written to {args.json}")


if __name__ == "__main__":
    main()
