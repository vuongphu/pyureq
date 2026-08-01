# pyureq

A `requests`-compatible Python HTTP library backed by Rust's
[ureq](https://github.com/algesten/ureq) crate.  Drop-in replacement for
`requests` — same API, lower overhead, no external runtime dependency.

[![CI](https://github.com/vuongphu/pyureq/actions/workflows/ci.yml/badge.svg)](https://github.com/vuongphu/pyureq/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pyureq.svg)](https://pypi.org/project/pyureq/)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/pyureq.svg)](https://pypi.org/project/pyureq/)
[![Downloads](https://static.pepy.tech/personalized-badge/pyureq?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/pyureq)

## Installation

```
pip install pyureq
```

Pre-built wheels are available for Linux, macOS, and Windows (Python 3.9–3.14).
The wheel bundles its own OpenSSL, so no system dependency is required.

## Quick start

```python
import pyureq

# Simple GET
r = pyureq.get("https://httpbin.org/get", params={"q": "hello"})
print(r.status_code)   # 200
print(r.json())

# POST JSON
r = pyureq.post("https://httpbin.org/post", json={"key": "value"})
print(r.json()["json"])

# POST form data
r = pyureq.post("https://httpbin.org/post", data={"field": "hello"})
print(r.json()["form"])

# With timeout and headers
r = pyureq.get(
    "https://httpbin.org/get",
    headers={"X-Custom": "header"},
    timeout=5,
)
r.raise_for_status()
```

### Alias as `requests`

Existing code that imports `requests` can be pointed at `pyureq` with a
one-line change:

```python
import pyureq as requests          # <- only change needed

r = requests.get("https://example.com")
print(r.text)
```

## Sessions

`pyureq.Session` maps directly to `requests.Session` and shares the same
connection pool across requests, so repeated calls to the same host reuse a
single TCP connection:

```python
import pyureq

with pyureq.Session() as s:
    s.headers.update({"Authorization": "Bearer mytoken"})
    s.timeout = 10

    r = s.get("https://api.example.com/users")
    r = s.post("https://api.example.com/items", json={"name": "widget"})
```

## API reference

### Top-level functions

| Function | Description |
|---|---|
| `pyureq.get(url, **kwargs)` | GET |
| `pyureq.post(url, data=None, json=None, **kwargs)` | POST |
| `pyureq.put(url, data=None, **kwargs)` | PUT |
| `pyureq.patch(url, data=None, **kwargs)` | PATCH |
| `pyureq.delete(url, **kwargs)` | DELETE |
| `pyureq.head(url, **kwargs)` | HEAD |
| `pyureq.options(url, **kwargs)` | OPTIONS |
| `pyureq.request(method, url, **kwargs)` | Any method |
| `pyureq.session()` | Create a `Session` |

### Common keyword arguments

| Argument | Type | Description |
|---|---|---|
| `params` | `dict` or `list[tuple]` | Query string parameters |
| `data` | `dict`, `str`, or `bytes` | Form-encoded or raw request body |
| `json` | any JSON-serializable | JSON request body |
| `headers` | `dict` | Extra HTTP headers |
| `cookies` | `dict` | Cookies to send |
| `auth` | `(user, password)` | HTTP Basic Auth |
| `timeout` | `float` | Seconds before timing out |
| `allow_redirects` | `bool` | Follow redirects (default `True`) |
| `verify` | `bool` | Verify TLS certificate (default `True`) |
| `proxies` | `dict` | Proxy URLs keyed by scheme — see [Proxies](#proxies) |

### `Response` attributes and methods

| Name | Description |
|---|---|
| `.status_code` | Integer status (e.g. `200`) |
| `.headers` | Case-insensitive response headers |
| `.content` | Response body as `bytes` |
| `.text` | Response body decoded to `str` |
| `.encoding` | Encoding used for `.text` (readable/writable) |
| `.url` | Final URL after redirects |
| `.ok` | `True` if status < 400 |
| `.elapsed` | `datetime.timedelta` of round-trip time |
| `.json(**kwargs)` | Parse body as JSON |
| `.raise_for_status()` | Raise `HTTPError` on 4xx/5xx |

## Proxies

Pass a `proxies` dict keyed by URL scheme, exactly as you would with `requests`:

```python
PROXIES = {
    "http":  "http://proxy.example.com:8080",
    "https": "http://user:pass@proxy.example.com:8080",
}

r = pyureq.get("https://api.example.com", proxies=PROXIES)
```

SOCKS proxies are supported too:

```python
PROXIES = {
    "http":  "socks5://proxy.example.com:1080",
    "https": "socks5://proxy.example.com:1080",
}
```

Use `"all"` as a catch-all key (same as `requests`):

```python
pyureq.get(url, proxies={"all": "http://proxy:8080"})
```

Set proxies session-wide via `Session.proxies`; per-request `proxies=`
overrides individual schemes:

```python
with pyureq.Session() as s:
    s.proxies = {"https": "http://proxy:8080"}
    s.get("https://api.example.com")                       # uses proxy
    s.get("https://api.example.com", proxies={"https": ""})  # bypass proxy
```

Pass an empty dict or empty-string values to disable system proxies
(`HTTP_PROXY` / `HTTPS_PROXY` env vars):

```python
pyureq.get(url, proxies={"http": "", "https": ""})   # no proxy
```

## Exceptions

All exceptions live under `pyureq.exceptions` and mirror the `requests`
exception hierarchy:

```
RequestException
├── ConnectionError
│   └── ProxyError
│       └── SSLError
├── HTTPError
├── URLRequired
├── TooManyRedirects
├── Timeout
│   ├── ConnectTimeout
│   └── ReadTimeout
└── ...
```

```python
import pyureq
from pyureq.exceptions import Timeout, HTTPError

try:
    r = pyureq.get("https://example.com", timeout=2)
    r.raise_for_status()
except Timeout:
    print("Request timed out")
except HTTPError as e:
    print(f"HTTP error: {e.response.status_code}")
```

## Building from source

Requires [Rust](https://rustup.rs/) and
[maturin](https://github.com/PyO3/maturin):

```bash
pip install maturin
maturin build --release
pip install target/wheels/*.whl
```

For a development install (rebuild-on-change):

```bash
pip install maturin
maturin develop
```

## Running tests

```bash
pip install pytest flask
pytest tests/ -v
```

## Benchmarks

All benchmarks run against a local server to eliminate network jitter.

### Single-thread latency

Mean latency (ms) per request — lower is better.

```
Scenario             pyureq   requests   curl_cffi    httpx†
──────────────────────────────────────────────────────────────
GET /get               1.58       2.59        2.46       2.39
GET /get (params)      1.45       2.68        2.36       2.37
POST JSON              1.56       2.74        2.16       2.39
POST form              1.68       3.33        2.17       2.17
GET /status/200        1.24       2.43        2.03       1.85
GET /headers           1.44       2.72        2.37       2.37
──────────────────────────────────────────────────────────────
Overall mean           1.49       2.75        2.26       2.26
                       ×1.0    ×1.85 slower ×1.52 slower ×1.52 slower
```

_†httpx measured with `Client()` session; `httpx.get()` stateless has ~68 ms
per-call overhead (creates/destroys a full transport each time)._

### Concurrency — 1 000 simultaneous threads

Each thread makes 5 sequential requests (5 000 total). Higher req/s and lower
p99 latency is better; fewer errors is better.

```
Mode        Library      req/s    p99 ms    errors
──────────────────────────────────────────────────
stateless   pyureq         268      7 242      26 / 5000  ← fewest errors
            curl_cffi     226     10 039     457 / 5000
            requests      203     15 248     404 / 5000

session     pyureq         261      9 206      46 / 5000
            curl_cffi     232     10 038     450 / 5000
            requests      244     10 015     327 / 5000
```

At 1 000 threads pyureq delivers **19 % more throughput** and **17× fewer
timeouts** than curl_cffi in stateless mode.  The advantage comes from lower
Python-layer overhead per request: because pyureq's response parsing happens
entirely in Rust, it holds the GIL for a shorter window (~0.3 ms vs ~0.8 ms),
reducing GIL pile-up under heavy concurrency.

### GIL behaviour

All four libraries release the GIL during network I/O — background Python
threads can run while requests are in flight.  The difference is *how much*
Python overhead each library runs with the GIL held before and after each I/O
call.

```
Library      GIL held per request (approx)
──────────────────────────────────────────
pyureq        ~0.3 ms   (parsing in Rust)
curl_cffi     ~0.8 ms   (CFFI + Python result handling)
requests      ~1.2 ms   (urllib3 pure-Python stack)
```

Run the benchmarks yourself:

```bash
pip install requests httpx curl_cffi

python benchmarks/bench.py          --iterations 500   # latency
python benchmarks/bench_threads.py  --requests 5       # concurrency
python benchmarks/bench_gil.py      --iterations 200   # GIL behaviour
```

## How it works

```
Python call
    │
    ▼
pyureq Python layer   ← thin: arg normalisation, exception mapping
    │
    │  py.detach()  ← GIL released here
    ▼
Rust / ureq         ← HTTP request + response parsing, all in Rust
    │
    ▼
native-tls          ← system TLS on macOS/Windows, bundled OpenSSL on Linux
    │
    ▼
TCP socket
```

The GIL is released for the entire Rust call — connection, TLS handshake,
`send`, `recv`, and response parsing all happen without blocking other Python
threads.  The GIL is re-acquired only to build the final Python `Response`
object.

The Rust core uses [ureq](https://github.com/algesten/ureq), a synchronous
(no async runtime) HTTP/1.1 client built on Rust's standard blocking I/O.

## License

MIT
