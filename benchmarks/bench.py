"""
Benchmark: pyureq vs requests vs httpx vs curl_cffi
==================================================

Runs against a local Flask server to eliminate network jitter.

Usage
-----
    # quick run (default: 200 iterations per scenario)
    python benchmarks/bench.py

    # custom iteration count
    python benchmarks/bench.py --iterations 500

    # save results to JSON
    python benchmarks/bench.py --json results.json
"""

import argparse
import json
import statistics
import sys
import threading
import time
from typing import Callable

# ---------------------------------------------------------------------------
# Local test server
# ---------------------------------------------------------------------------

sys.path.insert(0, "tests")
from local_server import start_server

PORT = 19999
BASE = f"http://127.0.0.1:{PORT}"


# ---------------------------------------------------------------------------
# Library imports (graceful degradation if not installed)
# ---------------------------------------------------------------------------

LIBS: dict[str, object] = {}

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
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, Callable]] = [
    (
        "GET /get",
        lambda lib: lib.get(f"{BASE}/get"),
    ),
    (
        "GET /get  (params)",
        lambda lib: lib.get(f"{BASE}/get", params={"q": "hello", "page": "1"}),
    ),
    (
        "POST JSON",
        lambda lib: lib.post(f"{BASE}/post", json={"key": "value", "num": 42}),
    ),
    (
        "POST form",
        lambda lib: lib.post(f"{BASE}/post", data={"field": "hello", "other": "world"}),
    ),
    (
        "GET /status/200",
        lambda lib: lib.get(f"{BASE}/status/200"),
    ),
    (
        "GET /headers",
        lambda lib: lib.get(f"{BASE}/get", headers={"X-Custom": "benchmark", "Accept": "application/json"}),
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _warmup(lib, scenario_fn, n: int = 5):
    for _ in range(n):
        try:
            scenario_fn(lib)
        except Exception:
            pass


def _run(lib, scenario_fn, n: int) -> list[float]:
    times: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            scenario_fn(lib)
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            return []
        times.append(time.perf_counter() - t0)
    return times


def _stats(times: list[float]) -> dict:
    if not times:
        return {"mean_ms": float("nan"), "p50_ms": float("nan"),
                "p95_ms": float("nan"), "ops_sec": 0.0}
    ms = [t * 1000 for t in times]
    sorted_ms = sorted(ms)
    p95_idx = int(len(sorted_ms) * 0.95)
    return {
        "mean_ms": statistics.mean(ms),
        "p50_ms": sorted_ms[len(sorted_ms) // 2],
        "p95_ms": sorted_ms[p95_idx],
        "ops_sec": 1000 / statistics.mean(ms),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

COL_SCENARIO = 24
COL_LIB      = 13
COL_MEAN     = 10
COL_P50      = 10
COL_P95      = 10
COL_OPS      = 10

def _header():
    print(
        f"{'Scenario':<{COL_SCENARIO}} "
        f"{'Library':<{COL_LIB}} "
        f"{'mean(ms)':>{COL_MEAN}} "
        f"{'p50(ms)':>{COL_P50}} "
        f"{'p95(ms)':>{COL_P95}} "
        f"{'ops/sec':>{COL_OPS}}"
    )
    total = COL_SCENARIO + 1 + COL_LIB + 1 + COL_MEAN + 1 + COL_P50 + 1 + COL_P95 + 1 + COL_OPS
    print("-" * total)


def _row(scenario: str, lib_name: str, st: dict, baseline_mean: float | None):
    mean = st["mean_ms"]
    speedup = ""
    if baseline_mean and baseline_mean > 0 and not (mean != mean):  # not NaN
        ratio = baseline_mean / mean
        if ratio >= 1.0:
            speedup = f"  ×{ratio:.2f} faster"
        else:
            speedup = f"  ×{1/ratio:.2f} slower"
    print(
        f"{'':>{COL_SCENARIO}} "   if scenario == "" else
        f"{scenario:<{COL_SCENARIO}} ",
        end="",
    )
    print(
        f"{lib_name:<{COL_LIB}} "
        f"{mean:>{COL_MEAN}.3f} "
        f"{st['p50_ms']:>{COL_P50}.3f} "
        f"{st['p95_ms']:>{COL_P95}.3f} "
        f"{st['ops_sec']:>{COL_OPS}.1f}"
        f"{speedup}"
    )


def _sep():
    total = COL_SCENARIO + 1 + COL_LIB + 1 + COL_MEAN + 1 + COL_P50 + 1 + COL_P95 + 1 + COL_OPS
    print("·" * total)


# ---------------------------------------------------------------------------
# Session benchmarks (persistent connection pool)
# ---------------------------------------------------------------------------

def _session_scenarios(n: int) -> dict:
    """Run each library with a persistent session and return results."""
    results = {}
    sessions = {}

    # Create sessions
    if "pyureq" in LIBS:
        sessions["pyureq"] = pyureq.Session()
    if "requests" in LIBS:
        sessions["requests"] = LIBS["requests"].Session()
    if "httpx" in LIBS:
        sessions["httpx"] = LIBS["httpx"].Client()
    if "curl_cffi" in LIBS:
        sessions["curl_cffi"] = LIBS["curl_cffi"].Session()

    scenario_name = "GET /get (session)"
    scenario_fn = lambda s: s.get(f"{BASE}/get")
    results[scenario_name] = {}

    for lib_name, sess in sessions.items():
        _warmup(sess, scenario_fn)
        times = _run(sess, scenario_fn, n)
        results[scenario_name][lib_name] = _stats(times)

    # Close sessions
    for sess in sessions.values():
        try:
            sess.close()
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HTTP library benchmark")
    parser.add_argument("--iterations", "-n", type=int, default=200,
                        help="Requests per scenario (default: 200)")
    parser.add_argument("--json", metavar="FILE",
                        help="Write results to JSON file")
    parser.add_argument("--warmup", type=int, default=5,
                        help="Warmup iterations (default: 5)")
    args = parser.parse_args()

    print(f"\nStarting local server on port {PORT}...")
    start_server(PORT)
    print(f"Server ready.  Libraries: {', '.join(LIBS)}")
    print(f"Iterations: {args.iterations}  Warmup: {args.warmup}\n")

    all_results: dict = {}

    # -- Stateless (one-shot per request) -----------------------------------
    print("═" * 80)
    print("  STATELESS (new connection each request)")
    print("═" * 80)
    _header()

    for scenario_name, scenario_fn in SCENARIOS:
        baseline_mean = None
        first = True
        for lib_name, lib in LIBS.items():
            _warmup(lib, scenario_fn, args.warmup)
            times = _run(lib, scenario_fn, args.iterations)
            st = _stats(times)

            if lib_name not in all_results:
                all_results[lib_name] = {}
            all_results[lib_name][scenario_name] = st

            _row(scenario_name if first else "", lib_name, st, baseline_mean)
            first = False
            if lib_name == "requests" and not (st["mean_ms"] != st["mean_ms"]):
                baseline_mean = st["mean_ms"]

        _sep()

    # -- Session (persistent pool) ------------------------------------------
    print()
    print("═" * 80)
    print("  SESSION (persistent connection pool)")
    print("  Note: httpx stateless mode is slow because httpx.get() creates")
    print("  and tears down a full Client+transport per call; use Session for")
    print("  fair async-ready comparison.")
    print("═" * 80)
    _header()

    session_results = _session_scenarios(args.iterations)
    for scenario_name, lib_stats in session_results.items():
        baseline_mean = lib_stats.get("requests", {}).get("mean_ms")
        first = True
        for lib_name, st in lib_stats.items():
            _row(scenario_name if first else "", lib_name, st, baseline_mean)
            first = False
        _sep()

    # -- Summary ------------------------------------------------------------
    print()
    print("═" * 80)
    print("  SUMMARY: mean latency across all stateless scenarios (ms)")
    print("═" * 80)
    col_w = 14
    lib_names = list(LIBS.keys())
    print(f"  {'':30}", end="")
    for n in lib_names:
        print(f"{n:>{col_w}}", end="")
    print()
    print("  " + "-" * (30 + col_w * len(lib_names)))

    for scenario_name, _ in SCENARIOS:
        print(f"  {scenario_name:<30}", end="")
        for lib_name in lib_names:
            val = all_results.get(lib_name, {}).get(scenario_name, {}).get("mean_ms", float("nan"))
            print(f"{val:>{col_w}.3f}", end="")
        print()

    # Overall means
    print("  " + "-" * (30 + col_w * len(lib_names)))
    print(f"  {'Overall mean':<30}", end="")
    for lib_name in lib_names:
        vals = [
            all_results.get(lib_name, {}).get(sn, {}).get("mean_ms", float("nan"))
            for sn, _ in SCENARIOS
        ]
        valid = [v for v in vals if v == v]  # drop NaN
        overall = statistics.mean(valid) if valid else float("nan")
        print(f"{overall:>{col_w}.3f}", end="")
    print()
    print()

    # -- JSON output --------------------------------------------------------
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"iterations": args.iterations, "results": all_results,
                       "session": session_results}, f, indent=2)
        print(f"Results written to {args.json}")


if __name__ == "__main__":
    main()
