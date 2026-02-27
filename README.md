# rupy

A `requests`-compatible Python HTTP library backed by Rust's
[ureq](https://github.com/algesten/ureq) crate.  Drop-in replacement for
`requests` — same API, lower overhead, no external runtime dependency.

[![CI](https://github.com/vuongphu/rupy/actions/workflows/ci.yml/badge.svg)](https://github.com/vuongphu/rupy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/rupy.svg)](https://pypi.org/project/rupy/)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/rupy.svg)](https://pypi.org/project/rupy/)

## Installation

```
pip install rupy
```

Pre-built wheels are available for Linux, macOS, and Windows (Python 3.9+).
The wheel bundles its own OpenSSL, so no system dependency is required.

## Quick start

```python
import rupy

# Simple GET
r = rupy.get("https://httpbin.org/get", params={"q": "hello"})
print(r.status_code)   # 200
print(r.json())

# POST JSON
r = rupy.post("https://httpbin.org/post", json={"key": "value"})
print(r.json()["json"])

# POST form data
r = rupy.post("https://httpbin.org/post", data={"field": "hello"})
print(r.json()["form"])

# With timeout and headers
r = rupy.get(
    "https://httpbin.org/get",
    headers={"X-Custom": "header"},
    timeout=5,
)
r.raise_for_status()
```

### Alias as `requests`

Existing code that imports `requests` can be pointed at `rupy` with a
one-line change:

```python
import rupy as requests          # <- only change needed

r = requests.get("https://example.com")
print(r.text)
```

## Sessions

`rupy.Session` maps directly to `requests.Session` and shares the same
connection pool across requests:

```python
import rupy

with rupy.Session() as s:
    s.headers.update({"Authorization": "Bearer mytoken"})
    s.timeout = 10

    r = s.get("https://api.example.com/users")
    r = s.post("https://api.example.com/items", json={"name": "widget"})
```

## API reference

### Top-level functions

| Function | Description |
|---|---|
| `rupy.get(url, **kwargs)` | GET |
| `rupy.post(url, data=None, json=None, **kwargs)` | POST |
| `rupy.put(url, data=None, **kwargs)` | PUT |
| `rupy.patch(url, data=None, **kwargs)` | PATCH |
| `rupy.delete(url, **kwargs)` | DELETE |
| `rupy.head(url, **kwargs)` | HEAD |
| `rupy.options(url, **kwargs)` | OPTIONS |
| `rupy.request(method, url, **kwargs)` | Any method |
| `rupy.session()` | Create a `Session` |

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

## Exceptions

All exceptions live under `rupy.exceptions` and mirror the `requests`
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
import rupy
from rupy.exceptions import Timeout, HTTPError

try:
    r = rupy.get("https://example.com", timeout=2)
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

Measured against a local Flask server (eliminates network jitter).
All numbers are mean latency in milliseconds; lower is better.

```
Scenario               rupy     requests    httpx*   curl_cffi
─────────────────────────────────────────────────────────────────
GET /get               1.58       2.59       2.39       2.46
GET /get (params)      1.45       2.68       2.37       2.36
POST JSON              1.56       2.74       2.39       2.16
POST form              1.68       3.33       2.17       2.17
GET /status/200        1.24       2.43       1.85       2.03
GET /headers           1.44       2.72       2.37       2.37
─────────────────────────────────────────────────────────────────
Overall mean           1.49       2.75       2.26       2.26
```

_*httpx session (`Client`) used for fair comparison; `httpx.get()` stateless
has ~68 ms overhead per call because it creates and tears down a full
transport on every request._

Run the benchmark yourself:

```bash
pip install requests httpx curl_cffi
python benchmarks/bench.py --iterations 500
```

## How it works

```
Python call → rupy Python layer
                  │ (GIL released via py.allow_threads)
                  ↓
             Rust / ureq
                  │
             TLS (native-tls / system OpenSSL)
                  │
             TCP socket
```

The Python layer converts arguments and responses between Python objects and
Rust types.  The GIL is released for the duration of each network call, so
the Rust HTTP code runs truly concurrently with other Python threads.

The Rust core uses [ureq](https://github.com/algesten/ureq), a blocking
(no async runtime) HTTP/1.1 client.  TLS is handled by `native-tls`, which
links against the system's TLS library on macOS and Windows and a bundled
OpenSSL on Linux.

## License

MIT
