# Changelog

All notable changes to pyureq are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Changed
- **Benchmarks only — no library code changed.** `bench_stress.py` reported
  `req/s` as *attempted* requests per second, counting failures as throughput.
  Because a refused connection returns in microseconds, a library that failed
  99% of the time outscored one serving every request. The 2026-08 Windows CI
  run ranked `requests` above pyureq at 50 threads, 824 vs 633 req/s — on 18
  successful requests against pyureq's 3235.

  The headline metric is now **good/s** (HTTP 200s per second); the attempt rate
  is kept as a `try/s` column for diagnosis, and `err_conn` is broken out
  alongside `t/o`. Numbers from this benchmark are not comparable across this
  change.

  Three related fixes to the same harness:
  - The test server now serves **HTTP/1.1** (`start_server(keep_alive=True)`).
    Werkzeug's HTTP/1.0 default closes the socket after every response, so the
    SESSION sweep had nothing to pool and silently re-measured STATELESS. The
    pytest suite keeps the HTTP/1.0 default; all 135 tests still pass.
  - A `--cooldown` drain (default 30s on Windows) runs between libraries.
    Libraries are benchmarked sequentially, so whoever ran first spent the
    host's ephemeral-port budget on everyone after it.
  - The break line now distinguishes host limits from library faults: failures
    that are almost entirely connect-time point at ephemeral-port exhaustion,
    which is a property of the machine and the offered load. The sweep offered
    ~615 new conn/s against a Windows ceiling of ~137 (16384 ports / 120s
    TIME_WAIT); Linux, at ~470/s plus tuple reuse, showed 0% errors throughout.
    CI now widens the Windows port range and caps the default sweep at 100
    threads.

  With these applied, Windows reports 0.00% errors at every level and pyureq
  leads both modes (1352 good/s stateless, 1227 session, vs curl_cffi's 915 /
  983 and requests' 730 / 723).

---

## [0.1.7] — 2026-08-06

### Fixed
- **Headers you set explicitly were sent twice.** When a request supplied a
  header that pyureq also derives on its own, both copies went out on the
  wire:

  | you pass | pyureq also derives | result |
  |---|---|---|
  | `headers={"Content-Type": ...}` + `json=` / `data=` | `Content-Type` | sent 2× |
  | `headers={"Cookie": ...}` + `cookies=` | `Cookie` | sent 2× |
  | `headers={"Authorization": ...}` + `auth=` | `Authorization` | sent 2× |

  This is a regression introduced in 0.1.6 by the ureq 2 → ureq 3 migration.
  ureq 2's `Request::set` **replaced** an existing header; ureq 3 builds
  through `http::request::Builder::header`, which calls `try_append` and
  therefore **appends**. The three call sites were migrated verbatim, so each
  derived header stacked on top of the caller's.

  Most servers tolerate a repeated header, which is why this went unnoticed —
  it only breaks against strict gateways. Crypto.com's exchange API answers a
  duplicated `Content-Type` with `{"code": 50001, "message": "ERR_INTERNAL"}`,
  a generic error that points nowhere near the real cause; the same request
  through `requests` succeeded, making it look like a server-side or
  authentication problem.

  pyureq now follows `requests`' rule: a derived header is only applied when
  the caller did not supply one, so an explicit header always wins. Matching
  is case-insensitive (`content-type` and `Content-Type` are the same header
  per RFC 9110). Deriving still works unchanged when you pass no header of
  your own.

### Testing
- New `tests/test_duplicate_headers.py` (12 tests) asserts on the **raw
  request bytes** read off a socket. The existing WSGI/Flask test server
  collapses repeated headers into a single mapping entry, so it structurally
  could not observe this bug. Verified by rebuilding with the fix reverted:
  8 of the 12 fail, while the four "derived headers still work" cases keep
  passing — the suite targets the defect rather than pinning current
  behaviour. Full suite: 135 passing.

---

## [0.1.6] — 2026-08-01

### Fixed
- **Timeouts were reported as `ConnectionError` instead of `Timeout`.** The
  transport error was classified by searching the OS error message for
  `"timed out"`/`"deadline"`. Windows reports `WSAETIMEDOUT` (os error 10060)
  with a message containing neither phrase, so every timeout on Windows raised
  `ConnectionError`. Errors are now matched on ureq's typed `Error::Timeout`
  variant, so classification is platform-independent.
- **`Session` did not pool connections.** A new agent — and therefore a new
  connection pool — was constructed for *every* request, so five sequential
  `Session.get()` calls opened five TCP connections despite the documented
  keep-alive behaviour. `Session` now holds one long-lived agent, so repeated
  requests to the same host reuse a single connection.
- **`deflate` responses came back still compressed.** The default
  `Accept-Encoding` advertised `gzip, deflate`, but the Rust core only decodes
  gzip and brotli. A server honouring `deflate` returned raw zlib bytes, and
  `.text` / `.json()` failed with `UnicodeDecodeError`. `deflate` is no longer
  advertised. (Same class of bug as the brotli fix in 0.1.3.)
- **Per-request `verify=` was ignored on the `Session` path.** `verify` was
  never forwarded to the Rust client, so `session.get(url, verify=False)`
  still performed full certificate verification. It is now honoured, and a
  per-request `verify` gets its own connection pool: ureq keys pooled
  connections on `(scheme, authority, proxy)` without regard to TLS config, so
  sharing one pool would have let a `verify=False` connection be reused to
  serve a later `verify=True` request — silently skipping validation.

### Changed
- Upgraded **ureq 2.12 → 3.3** and **pyo3 0.23 → 0.29**.
- Dropped the unused `serde_json` dependency and ureq's default features, which
  were compiling a second TLS stack (rustls + ring + webpki) that the crate
  never used. TLS is native-tls only, so the OS trust store stays authoritative
  — preserving the 0.1.5 `UnknownIssuer` fix.
- Response bodies are no longer capped at ureq 3's default 10 MB read limit.
- The extension is now declared free-threading safe (`gil_used = false`); it
  holds no Python state while performing I/O.
- Building from source now requires Rust 1.85 or newer (a ureq 3 requirement).
  Installing from a published wheel is unaffected.

### Added
- Regression tests covering all four fixes above, including a hermetic
  self-signed HTTPS fixture for the `verify` cases — no network access needed.
- Type stubs now match the real extension signatures; `RustClient.__init__` was
  missing `timeout`/`no_proxy_all` and `RustClient.request` was missing `proxy`.

[0.1.7]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.7
[0.1.6]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.6

---

## [0.1.5] — 2026-04-05

### Fixed
- **TLS `UnknownIssuer` error on some HTTPS domains** — The `native-tls`
  backend (via `schannel 0.1.28` on Windows) changed how it initialises the
  TLS connector, causing certificate chain validation to fail for domains whose
  intermediate CA had not previously been cached by Windows.  The fix is to
  always construct an explicit `TlsConnector` (even when `verify=True`) so that
  the OS trust store is properly loaded on every platform (Windows CryptoAPI,
  macOS Security framework, Linux OpenSSL).

### Added
- `verify` now accepts a **path string** to a PEM CA-bundle file, matching
  `requests` semantics:
  - `verify=True` (default) — system / OS CA store
  - `verify=False` — skip all certificate verification
  - `verify="/path/to/ca-bundle.pem"` — use the supplied PEM file

[0.1.5]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.5

---

## [0.1.4] — 2026-02-28

### Added
- Proxy support via the `proxies` parameter, matching the `requests` API:
  `proxies={"http": "http://host:port", "https": "http://user:pass@host:port"}`.
  HTTP, HTTPS, SOCKS4, SOCKS4A, SOCKS5, and SOCKS5H proxy URLs are all
  accepted.  `Session.proxies` dict is also supported for session-wide defaults.

[0.1.4]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.4

---

## [0.1.3] — 2026-02-28

### Fixed
- Remove `br` from the default `Accept-Encoding` session header.  pyureq was
  advertising brotli support it could not fulfil, causing servers to return
  brotli-compressed bodies that the Rust backend cannot decompress.  The raw
  bytes then raised `UnicodeDecodeError` when callers accessed `.text` or
  `.json()`.

[0.1.3]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.3

---

## [0.1.2] — 2026-02-28

### Fixed
- Bytes header values are now decoded with latin-1 (matching `requests`
  behaviour) instead of `str()`, which produced `"b'...'"` and caused HMAC
  signature verification failures (`40009 sign signature error`) on APIs such
  as Bitget.

### Removed
- `SessionPool` / `pool.py` — removed from the public API.

[0.1.2]: https://github.com/vuongphu/pyureq/releases/tag/v0.1.2

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
