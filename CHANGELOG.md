# Changelog

All notable changes to pyureq are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.1.0] — 2026-02-28

### Added

- Initial release of **pyureq** — a `requests`-compatible Python HTTP library
  backed by Rust's [ureq](https://github.com/algesten/ureq) crate.
- Full `requests`-compatible surface:
  - `pyureq.get / post / put / patch / delete / head / options / request`
  - `Session` with connection pooling, shared headers, cookies, auth, and
    timeout defaults
  - `Response` with `.status_code`, `.text`, `.content`, `.json()`,
    `.headers`, `.url`, `.ok`, `.elapsed`, `.raise_for_status()`, and more
  - `CaseInsensitiveDict` for response headers
  - Complete `requests.exceptions` hierarchy
    (`Timeout`, `HTTPError`, `ConnectionError`, `TooManyRedirects`, …)
- Rust core (`_pyureq` extension module) built with
  [PyO3](https://github.com/PyO3/pyo3) + ureq 2.x:
  - GIL released for the entire network call (connection → TLS → send →
    recv → response parsing)
  - `RustClient` for persistent connection pooling per `Session`
  - One-shot stateless `http_request` for top-level convenience functions
  - `native-tls` for TLS (system TLS on macOS/Windows, bundled OpenSSL
    on Linux)
  - `verify=False` support via `danger_accept_invalid_certs`
  - Basic Auth, custom headers, cookies, query params, timeout, redirects,
    and proxy disable (`proxies={"http": "", "https": ""}`)
- Pre-built `abi3-py39` wheels for Linux, macOS, Windows (x86-64 and
  ARM64 on macOS)
- Distributed as `pyureq` on PyPI; import name is `pyureq`
- GitHub Actions CI: lint (ruff + clippy), test matrix (Python 3.9–3.13),
  cross-platform wheel build, coverage report, and automated PyPI publish
  on `v*` tag push via OIDC Trusted Publishing

### Performance (vs requests, on local server)

| Metric | pyureq | requests |
|--------|--------|----------|
| Mean latency (single-thread) | 1.49 ms | 2.75 ms (+85%) |
| Throughput (1 000 threads) | 268 req/s | 203 req/s (+32%) |
| GIL hold per request | ~0.3 ms | ~1.2 ms |

[0.1.0]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.0
