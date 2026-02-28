"""Session and request-dispatch logic."""

import builtins
import json as _json
from urllib.parse import urlencode

from . import _pyureq
from .exceptions import (
    ConnectionError,
    RequestException,
    Timeout,
    TooManyRedirects,
)
from .models import Response
from .structures import CaseInsensitiveDict

# Python built-in ConnectionError — keep a reference before our import shadows it
_builtin_ConnectionError = builtins.ConnectionError

_DEFAULT_HEADERS = {
    "User-Agent": "pyureq/0.1.0",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "*/*",
    "Connection": "keep-alive",
}


def _normalize_headers(headers) -> dict:
    """Convert any header mapping to a plain ``{str: str}`` dict."""
    if headers is None:
        return {}

    def _coerce(v) -> str:
        # bytes/bytearray values must be decoded, not wrapped in str() which
        # produces "b'...'" and corrupts headers like HMAC signatures.
        # latin-1 matches the HTTP/1.1 header encoding used by requests.
        if isinstance(v, (bytes, bytearray)):
            return v.decode("latin-1")
        return str(v)

    if isinstance(headers, CaseInsensitiveDict):
        return {k: _coerce(v) for k, v in headers.items()}
    return {str(k): _coerce(v) for k, v in dict(headers).items()}


def _normalize_params(params) -> list:
    """Convert params (dict / list-of-tuples / None) to ``[(str, str)]``."""
    if params is None:
        return []
    if isinstance(params, dict):
        return [(str(k), str(v)) for k, v in params.items()]
    # list of 2-tuples
    return [(str(k), str(v)) for k, v in params]


def _normalize_cookies(cookies) -> dict:
    if cookies is None:
        return {}
    return {str(k): str(v) for k, v in dict(cookies).items()}


def _prepare_body(data=None, json=None):
    """Return ``(body_bytes, content_type)`` ready for the Rust core."""
    if json is not None:
        return _json.dumps(json, separators=(",", ":")).encode("utf-8"), "application/json"
    if data is not None:
        if isinstance(data, bytes):
            return data, None
        if isinstance(data, str):
            return data.encode("utf-8"), None
        if isinstance(data, dict):
            return urlencode(data).encode("utf-8"), "application/x-www-form-urlencoded"
    return None, None


def _dispatch(client_or_none, method, url, **kwargs):
    """Core dispatch: converts Python kwargs → Rust call → ``Response``."""
    merged_headers = _normalize_headers(kwargs.get("headers"))
    params = _normalize_params(kwargs.get("params"))
    cookies = _normalize_cookies(kwargs.get("cookies"))
    auth = kwargs.get("auth")
    timeout = kwargs.get("timeout")
    verify = kwargs.get("verify", True)
    allow_redirects = kwargs.get("allow_redirects", True)
    data = kwargs.get("data")
    json = kwargs.get("json")
    # proxies={} or proxies={"http": "", "https": ""} → disable all system proxies
    proxies = kwargs.get("proxies")
    no_proxy_all = isinstance(proxies, dict) and all(v == "" for v in proxies.values())

    body_bytes, content_type = _prepare_body(data, json)

    auth_tuple = (str(auth[0]), str(auth[1])) if auth else None
    timeout_f = float(timeout) if timeout is not None else None
    params_list = params or None
    cookies_dict = cookies or None
    headers_dict = merged_headers or None

    try:
        if client_or_none is not None:
            # Session path — reuse the Rust client (connection pooling)
            raw = client_or_none.request(
                method=method,
                url=url,
                headers=headers_dict,
                params=params_list,
                body=body_bytes,
                content_type=content_type,
                auth=auth_tuple,
                timeout=timeout_f,
                allow_redirects=allow_redirects,
                cookies=cookies_dict,
            )
        else:
            # Stateless path — create a one-shot client
            raw = _pyureq.http_request(
                method=method,
                url=url,
                headers=headers_dict,
                params=params_list,
                body=body_bytes,
                content_type=content_type,
                auth=auth_tuple,
                timeout=timeout_f,
                verify=bool(verify),
                allow_redirects=allow_redirects,
                cookies=cookies_dict,
                no_proxy_all=no_proxy_all,
            )
    except builtins.TimeoutError as exc:
        raise Timeout(str(exc)) from exc
    except (_builtin_ConnectionError, OSError) as exc:
        raise ConnectionError(str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc)
        if msg.startswith("TooManyRedirects"):
            raise TooManyRedirects(msg) from exc
        raise RequestException(msg) from exc
    except Exception as exc:
        raise RequestException(str(exc)) from exc

    return Response(raw)


class Session:
    """A Requests session.

    Provides cookie persistence, connection pooling, and configuration
    that persists across requests.  Compatible with ``requests.Session``.

    Usage::

        with pyureq.Session() as s:
            s.headers.update({"X-MyHeader": "value"})
            r = s.get("https://example.com")
    """

    def __init__(self):
        self.headers = CaseInsensitiveDict(_DEFAULT_HEADERS.copy())
        self.auth = None
        self.cookies: dict = {}
        self._verify = True
        self.params: dict = {}
        self.timeout = None
        self.allow_redirects = True

        # Persistent Rust client — connection pool lives here
        self._client = _pyureq.RustClient(verify=True)

    @property
    def verify(self) -> bool:
        """Whether to verify TLS certificates.  Changing this recreates the connection pool."""
        return self._verify

    @verify.setter
    def verify(self, value: bool):
        self._verify = bool(value)
        self._client = _pyureq.RustClient(verify=self._verify)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merge(self, kwargs: dict) -> dict:
        """Merge session-level defaults with per-request overrides."""
        merged = {}
        # Headers: session defaults + per-request overrides
        merged["headers"] = _normalize_headers({
            **_normalize_headers(self.headers),
            **_normalize_headers(kwargs.get("headers")),
        })
        # Params: session defaults + per-request
        merged["params"] = _normalize_params(self.params) + _normalize_params(kwargs.get("params"))
        # Cookies: session defaults + per-request
        merged["cookies"] = {**_normalize_cookies(self.cookies), **_normalize_cookies(kwargs.get("cookies"))}
        # Other settings: per-request takes precedence over session
        merged["auth"] = kwargs.get("auth", self.auth)
        merged["timeout"] = kwargs.get("timeout", self.timeout)
        merged["verify"] = kwargs.get("verify", self.verify)
        merged["allow_redirects"] = kwargs.get("allow_redirects", self.allow_redirects)
        merged["data"] = kwargs.get("data")
        merged["json"] = kwargs.get("json")
        return merged

    # ------------------------------------------------------------------
    # Public request methods
    # ------------------------------------------------------------------

    def request(self, method: str, url: str, **kwargs):
        """Constructs a :class:`Request`, prepares it and sends it."""
        merged = self._merge(kwargs)
        return _dispatch(self._client, method.upper(), url, **merged)

    def get(self, url, **kwargs):
        """Sends a GET request."""
        kwargs.setdefault("allow_redirects", True)
        return self.request("GET", url, **kwargs)

    def post(self, url, data=None, json=None, **kwargs):
        """Sends a POST request."""
        return self.request("POST", url, data=data, json=json, **kwargs)

    def put(self, url, data=None, **kwargs):
        """Sends a PUT request."""
        return self.request("PUT", url, data=data, **kwargs)

    def patch(self, url, data=None, **kwargs):
        """Sends a PATCH request."""
        return self.request("PATCH", url, data=data, **kwargs)

    def delete(self, url, **kwargs):
        """Sends a DELETE request."""
        return self.request("DELETE", url, **kwargs)

    def head(self, url, **kwargs):
        """Sends a HEAD request."""
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    def options(self, url, **kwargs):
        """Sends an OPTIONS request."""
        kwargs.setdefault("allow_redirects", True)
        return self.request("OPTIONS", url, **kwargs)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Closes the session.  The Rust client's pool is released on GC."""
        self._client = None

    def __repr__(self):
        return "<pyureq.Session>"
