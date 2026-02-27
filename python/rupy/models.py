"""Response model, matching the requests.Response API."""

import json as _json
from datetime import timedelta

from .exceptions import HTTPError
from .structures import CaseInsensitiveDict


class Response:
    """The :class:`Response <Response>` object, which contains a server's
    response to an HTTP request.

    Drop-in compatible with ``requests.Response``.
    """

    def __init__(self, raw):
        """
        Parameters
        ----------
        raw:
            A ``rupy._rupy.RawResponse`` instance from the Rust core.
        """
        self._raw = raw
        self._content: bytes = bytes(raw.body)
        self._headers = CaseInsensitiveDict(raw.headers)
        self._elapsed = timedelta(seconds=raw.elapsed_secs)
        self._encoding_override = None

    # ------------------------------------------------------------------
    # Core attributes
    # ------------------------------------------------------------------

    @property
    def status_code(self) -> int:
        """Integer status code of the HTTP response (e.g. 200, 404)."""
        return self._raw.status_code

    @property
    def headers(self) -> CaseInsensitiveDict:
        """Case-insensitive dict of response headers."""
        return self._headers

    @property
    def content(self) -> bytes:
        """Content of the response in bytes."""
        return self._content

    @property
    def encoding(self) -> str:
        """Encoding to decode ``text`` with.

        Prefers any explicitly set encoding, then the charset from the
        Content-Type header, falling back to ``utf-8``.
        """
        if self._encoding_override:
            return self._encoding_override
        # From Rust-extracted charset
        if self._raw.encoding:
            return self._raw.encoding
        # Fallback: scan content-type header ourselves
        ct = self._headers.get("content-type", "")
        for part in ct.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part[len("charset="):].strip().strip('"')
        return "utf-8"

    @encoding.setter
    def encoding(self, value: str):
        self._encoding_override = value

    @property
    def text(self) -> str:
        """Content of the response, in unicode."""
        return self.content.decode(self.encoding, errors="replace")

    @property
    def url(self) -> str:
        """Final URL of the response (after redirects)."""
        return self._raw.url

    @property
    def ok(self) -> bool:
        """``True`` if ``status_code`` is less than 400, ``False`` otherwise."""
        return self.status_code < 400

    @property
    def is_redirect(self) -> bool:
        """``True`` if this response is a redirect."""
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def elapsed(self) -> timedelta:
        """Time elapsed between sending the request and finishing parsing the response."""
        return self._elapsed

    @property
    def apparent_encoding(self) -> str:
        """The apparent encoding, provided by the chardet library (best-effort)."""
        try:
            import chardet
            detected = chardet.detect(self.content)
            return detected.get("encoding") or "utf-8"
        except ImportError:
            return self.encoding

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def json(self, **kwargs):
        """Return the json-encoded content of the response.

        Parameters
        ----------
        **kwargs:
            Optional arguments that ``json.loads`` takes.
        """
        return _json.loads(self.content, **kwargs)

    def raise_for_status(self):
        """Raise :class:`HTTPError`, if one occurred.

        Raises
        ------
        HTTPError
            If the response had a 4xx or 5xx status code.
        """
        if 400 <= self.status_code < 600:
            kind = "Client" if self.status_code < 500 else "Server"
            raise HTTPError(
                f"{self.status_code} {kind} Error: {self.url}",
                response=self,
            )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        """``True`` if the response is OK (status < 400)."""
        return self.ok

    def __repr__(self) -> str:
        return f"<Response [{self.status_code}]>"
