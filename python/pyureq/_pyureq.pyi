"""Type stubs for the pyureq Rust extension module (_pyureq).

These stubs enable IDE autocompletion and type-checking (mypy/pyright) for
the compiled Rust extension.  They mirror the PyO3 #[pyclass] and #[pymethods]
definitions in src/lib.rs.
"""

from typing import Optional

class RawResponse:
    """Raw HTTP response returned directly from the Rust core.

    Python code should use :class:`pyureq.models.Response` instead, which wraps
    this object and provides the full ``requests``-compatible API.
    """

    status_code: int
    """Integer HTTP status code (e.g. 200, 404)."""

    headers: dict[str, str]
    """Response headers as a plain ``{str: str}`` dict (last value wins for
    duplicate header names)."""

    body: bytes
    """Raw response body bytes."""

    url: str
    """Final URL after redirects."""

    elapsed_secs: float
    """Round-trip time in seconds."""

    encoding: str
    """Character encoding extracted from the ``Content-Type`` header, or an
    empty string if not present."""


class RustClient:
    """Persistent HTTP client with a connection pool.

    Created once per :class:`pyureq.Session` and reused across requests.  The
    underlying ``ureq`` agent keeps connections alive for efficiency.
    """

    def __init__(self, verify: bool = True) -> None:
        """
        Parameters
        ----------
        verify:
            Whether to verify TLS certificates.  Defaults to ``True``.
        """

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[list[tuple[str, str]]] = None,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        auth: Optional[tuple[str, str]] = None,
        timeout: Optional[float] = None,
        allow_redirects: bool = True,
        cookies: Optional[dict[str, str]] = None,
    ) -> RawResponse:
        """Execute an HTTP request using the pooled client.

        Parameters
        ----------
        method:
            HTTP method in uppercase (``"GET"``, ``"POST"``, etc.).
        url:
            Fully-qualified request URL.
        headers:
            Extra request headers as ``{name: value}``.
        params:
            Query-string parameters as a list of ``(name, value)`` pairs.
        body:
            Request body bytes, or ``None`` for requests with no body.
        content_type:
            Value for the ``Content-Type`` header.  If ``None`` and *body* is
            supplied, no ``Content-Type`` is set.
        auth:
            ``(username, password)`` tuple for HTTP Basic authentication.
        timeout:
            Total timeout in seconds.  ``None`` means no timeout.
        allow_redirects:
            Follow 3xx redirects when ``True`` (default).
        cookies:
            Cookies to send as ``{name: value}``.

        Returns
        -------
        RawResponse
            The server's response.

        Raises
        ------
        TimeoutError
            If the request exceeds *timeout* seconds.
        ConnectionError
            If a TCP/DNS error prevents the connection.
        RuntimeError
            For all other transport-level errors (e.g. too many redirects).
        """


def http_request(
    *,
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    params: Optional[list[tuple[str, str]]] = None,
    body: Optional[bytes] = None,
    content_type: Optional[str] = None,
    auth: Optional[tuple[str, str]] = None,
    timeout: Optional[float] = None,
    verify: bool = True,
    allow_redirects: bool = True,
    cookies: Optional[dict[str, str]] = None,
    no_proxy_all: bool = False,
) -> RawResponse:
    """Execute a one-shot (stateless) HTTP request.

    Creates a temporary ``ureq`` agent for this single request — no connection
    pooling.  Use :class:`RustClient` (via :class:`pyureq.Session`) for pooled
    access.

    Parameters
    ----------
    method:
        HTTP method in uppercase.
    url:
        Fully-qualified request URL.
    headers:
        Extra request headers.
    params:
        Query-string parameters as ``[(name, value)]``.
    body:
        Request body bytes.
    content_type:
        ``Content-Type`` header value.
    auth:
        ``(username, password)`` for Basic auth.
    timeout:
        Total timeout in seconds.
    verify:
        Verify TLS certificates (default ``True``).
    allow_redirects:
        Follow 3xx redirects (default ``True``).
    cookies:
        Cookies to send as ``{name: value}``.
    no_proxy_all:
        When ``True``, disable all proxy use (overrides environment variables).

    Returns
    -------
    RawResponse

    Raises
    ------
    TimeoutError
        On timeout.
    ConnectionError
        On TCP/DNS failure.
    RuntimeError
        On other transport errors.
    """


def tcp_probe(host: str, port: int) -> bool:
    """Attempt a raw TCP connection to *host*:*port*.

    Returns ``True`` if a connection could be established, ``False`` otherwise.
    Intended for debugging connectivity issues.
    """
