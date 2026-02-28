"""pyureq — A requests-compatible HTTP library powered by Rust's ureq.

Drop-in replacement for the ``requests`` package::

    import pyureq as requests        # alias it and existing code just works
    r = requests.get("https://example.com")
    print(r.status_code, r.text)

Or use the native API::

    import pyureq
    r = pyureq.get("https://httpbin.org/get", params={"q": "rust"})
    data = r.json()
"""

__version__ = "0.1.4"
__author__ = "pyureq contributors"

# Re-export the public surface that matches requests.*
from .exceptions import (  # noqa: F401
    ChunkedEncodingError,
    ConnectTimeout,
    ConnectionError,
    ContentDecodingError,
    HTTPError,
    InvalidHeader,
    InvalidSchema,
    InvalidURL,
    MissingSchema,
    ProxyError,
    ReadTimeout,
    RequestException,
    RetryError,
    SSLError,
    Timeout,
    TooManyRedirects,
    URLRequired,
)
from .models import Response  # noqa: F401
from .sessions import Session  # noqa: F401
from . import exceptions  # noqa: F401
from . import _pyureq  # noqa: F401 (Rust extension)

from .sessions import _dispatch as _make_request

# ---------------------------------------------------------------------------
# Top-level convenience functions (mirrors requests.*)
# ---------------------------------------------------------------------------


def request(method: str, url: str, **kwargs) -> Response:
    """Constructs and sends a :class:`Request <Response>`.

    Parameters
    ----------
    method:
        HTTP method, e.g. ``'GET'``, ``'POST'``.
    url:
        URL for the request.
    params:
        Dictionary, list of tuples or string to send in the query string.
    data:
        Dictionary, list of tuples, bytes, or file-like object to send in
        the body of the request.
    json:
        A JSON-serializable Python object to send in the body.
    headers:
        Dictionary of HTTP Headers to send.
    cookies:
        Dictionary of cookies to send.
    auth:
        Auth tuple (username, password) for Basic Auth.
    timeout:
        How many seconds to wait for the server to send data.
    allow_redirects:
        Enable/disable GET/OPTIONS/POST/PUT/PATCH/DELETE/HEAD redirection.
    verify:
        Either a boolean, in which case it controls whether we verify the
        server's TLS certificate, or a string, in which case it must be a
        path to a CA bundle.
    """
    return _make_request(None, method.upper(), url, **kwargs)


def get(url: str, params=None, **kwargs) -> Response:
    """Sends a GET request."""
    kwargs.setdefault("allow_redirects", True)
    return request("GET", url, params=params, **kwargs)


def post(url: str, data=None, json=None, **kwargs) -> Response:
    """Sends a POST request."""
    return request("POST", url, data=data, json=json, **kwargs)


def put(url: str, data=None, **kwargs) -> Response:
    """Sends a PUT request."""
    return request("PUT", url, data=data, **kwargs)


def patch(url: str, data=None, **kwargs) -> Response:
    """Sends a PATCH request."""
    return request("PATCH", url, data=data, **kwargs)


def delete(url: str, **kwargs) -> Response:
    """Sends a DELETE request."""
    return request("DELETE", url, **kwargs)


def head(url: str, **kwargs) -> Response:
    """Sends a HEAD request."""
    kwargs.setdefault("allow_redirects", False)
    return request("HEAD", url, **kwargs)


def options(url: str, **kwargs) -> Response:
    """Sends an OPTIONS request."""
    kwargs.setdefault("allow_redirects", True)
    return request("OPTIONS", url, **kwargs)


def session() -> Session:
    """Return a :class:`Session` for context-management."""
    return Session()
