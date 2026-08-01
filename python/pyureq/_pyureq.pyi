"""Type stubs for the pyureq Rust extension module (_pyureq).

These stubs enable IDE autocompletion and type-checking (mypy/pyright) for
the compiled Rust extension.  They mirror the PyO3 #[pyclass] and #[pymethods]
definitions in src/lib.rs.
"""


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

    encoding: str | None
    """Character encoding parsed from the ``Content-Type`` header's ``charset``
    parameter, or ``None`` if the header carries no charset."""


class RustClient:
    """Persistent HTTP client with a connection pool.

    Created once per :class:`pyureq.Session` and reused across requests.  The
    underlying ``ureq`` agent *is* the connection pool, so keep-alive works
    across calls on the same instance.

    A per-request ``verify`` that differs from the constructor value is served
    by a separate internal agent (and therefore a separate pool), so a
    connection opened with verification disabled is never reused for a
    verified request.
    """

    def __init__(
        self,
        verify: bool | str = True,
        timeout: float | None = None,
        no_proxy_all: bool = False,
    ) -> None:
        """
        Parameters
        ----------
        verify:
            TLS verification mode.  Mirrors ``requests`` semantics:

            - ``True``  (default) – verify using the OS / system CA store
            - ``False`` – skip all certificate verification (insecure)
            - ``"/path/to/ca-bundle.pem"`` – verify using the given PEM file
        timeout:
            Default total timeout in seconds applied to every request that does
            not supply its own.  ``None`` means no timeout.
        no_proxy_all:
            When ``True``, disable all proxy use for this client, including
            proxies configured via environment variables.
        """

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        verify: bool | str | None = None,
        allow_redirects: bool = True,
        cookies: dict[str, str] | None = None,
        no_proxy_all: bool = False,
        proxy: str | None = None,
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
            Total timeout in seconds.  Overrides the client default; ``None``
            falls back to the value given to :meth:`__init__`.
        verify:
            Per-request TLS verification mode.  ``None`` (default) uses the
            client's setting.  A differing value is served by a separate
            connection pool.
        allow_redirects:
            Follow 3xx redirects when ``True`` (default).
        cookies:
            Cookies to send as ``{name: value}``.
        no_proxy_all:
            When ``True``, disable all proxy use for this request.
        proxy:
            Explicit proxy URL for this request, e.g.
            ``"http://user:pass@host:port"``.

        Returns
        -------
        RawResponse
            The server's response.  4xx/5xx are returned normally, not raised.

        Raises
        ------
        TimeoutError
            If the request exceeds *timeout* seconds.
        ConnectionError
            If a TCP/DNS error prevents the connection.
        RuntimeError
            For all other transport-level errors (TLS failures, too many
            redirects, proxy errors, decompression failures).
        """


def http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: list[tuple[str, str]] | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float | None = None,
    verify: bool | str = True,
    allow_redirects: bool = True,
    cookies: dict[str, str] | None = None,
    no_proxy_all: bool = False,
    proxy: str | None = None,
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
        TLS verification mode — ``True`` (system CA store), ``False``
        (skip verification), or a path string to a PEM CA bundle file.
        Defaults to ``True``.
    allow_redirects:
        Follow 3xx redirects (default ``True``).
    cookies:
        Cookies to send as ``{name: value}``.
    no_proxy_all:
        When ``True``, disable all proxy use (overrides environment variables).
    proxy:
        Explicit proxy URL, e.g. ``"http://user:pass@host:port"``.  Supports
        http, https, socks4, socks4a, socks5 and socks5h schemes.

    Returns
    -------
    RawResponse
        The server's response.  4xx/5xx are returned normally, not raised.

    Raises
    ------
    TimeoutError
        On timeout.
    ConnectionError
        On TCP/DNS failure.
    RuntimeError
        On other transport errors (TLS, redirects, proxy, decompression).
    """


def tcp_probe(host: str, port: int) -> bool:
    """Attempt a raw TCP connection to *host*:*port*.

    Returns ``True`` if a connection could be established, ``False`` otherwise.
    Intended for debugging connectivity issues.
    """
