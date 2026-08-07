"""Regression tests for URL percent-encoding (fixed in 0.1.8).

ureq 2's `Request::set` accepted any string; ureq 3 hands the request URI to
the `http` crate's `Uri::try_from`, which requires RFC 3986 bytes.  Without
encoding, queries containing non-ASCII bytes went on the wire as raw UTF-8
and gateways refused to match the symbol -- exchanges would return empty
data arrays with no error, making it look like a server-side problem.

These tests read the raw request bytes off a socket, same as the
duplicate-headers suite.  A WSGI/Flask test server decodes the URL itself
before our code sees it, so it cannot observe this bug.
"""

import socket
import threading

import pytest
import pyureq


def _capture():
    """Start a one-shot raw TCP server; return (port, joiner)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    captured = []

    def run():
        try:
            conn, _ = srv.accept()
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65535)
                if not chunk:
                    break
                data += chunk
            captured.append(data.split(b"\r\n\r\n")[0].decode("latin1"))
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
            except OSError:
                pass
            conn.close()
        except OSError:
            captured.append("")
        finally:
            srv.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def joiner():
        thread.join(timeout=10)
        return captured[0] if captured else ""

    return port, joiner


EXPECTED = {
    "/x?symbol=龙虾USDT":       "/x?symbol=%E9%BE%99%E8%99%BEUSDT",
    "/龙虾/x":                   "/%E9%BE%99%E8%99%BE/x",
    "/?a=1+2&b=hello world":    "/?a=1+2&b=hello%20world",
    "/x?u=http://already%20encoded&k=plain":
        "/x?u=http://already%20encoded&k=plain",
    "/?utf8=谢谢":              "/?utf8=%E8%B0%A2%E8%B0%A2",
}


class TestUriEncoding:
    """Non-ASCII bytes in the request target must be percent-encoded.

    Expected values are pinned against what `requests.Request('GET', url)
    .prepare().path_url` produces (verified by hand for each case).  We
    don't import `requests` because it's not in the test dependency set;
    pinning is enough to catch regressions.
    """

    @pytest.mark.parametrize(
        "raw_path,expected_path",
        [
            (
                "/x?symbol=龙虾USDT",
                "GET /x?symbol=%E9%BE%99%E8%99%BEUSDT HTTP/1.1",
            ),
            (
                "/龙虾/x",
                "GET /%E9%BE%99%E8%99%BE/x HTTP/1.1",
            ),
            (
                "/?utf8=谢谢",
                "GET /?utf8=%E8%B0%A2%E8%B0%A2 HTTP/1.1",
            ),
        ],
    )
    def test_non_ascii_is_percent_encoded(self, raw_path, expected_path):
        port, joiner = _capture()
        try:
            pyureq.get(f"http://127.0.0.1:{port}{raw_path}", timeout=8, proxies={})
        except Exception:
            pass
        request_line = joiner().splitlines()[0]
        assert request_line == expected_path, (
            f"\n  got:  {request_line}\n  want: {expected_path}"
        )

    def test_existing_percent_encoding_preserved(self):
        """Caller-supplied %XX must NOT be double-encoded to %25XX."""
        port, joiner = _capture()
        try:
            pyureq.get(
                f"http://127.0.0.1:{port}/x?u=http://already%20encoded&k=plain",
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert "%20" in raw, f"\n{raw}"
        assert "%2520" not in raw, f"existing %XX was double-encoded:\n{raw}"

    def test_request_line_matches_pinned_expectations(self):
        """Wire bytes must equal the pinned parity for each case.

        Expected values come from running ``requests.Request('GET', url)
        .prepare().path_url`` for each input -- the test imports nothing
        extra and works on a hermetic CI that doesn't install `requests`.
        Pinning is enough to catch regressions; if the encoding diverges
        from `requests`, update both this dict and the encoder together.
        """
        for path, expected_path in EXPECTED.items():
            port, joiner = _capture()
            url = f"http://127.0.0.1:{port}{path}"
            try:
                pyureq.get(url, timeout=8, proxies={})
            except Exception:
                pass
            wire_path = joiner().splitlines()[0].split(" ", 1)[1].removesuffix(" HTTP/1.1")
            assert wire_path == expected_path, (
                f"\n  url:  {url}\n  got:  {wire_path}\n  want: {expected_path}"
            )

    def test_query_param_still_encoded_when_present(self):
        """URL params (`params=`) remain encoded independently and merged."""
        port, joiner = _capture()
        try:
            pyureq.get(
                f"http://127.0.0.1:{port}/x?symbol=龙虾USDT",
                params={"extra": "v龙虾"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner().splitlines()[0]
        assert "symbol=%E9%BE%99%E8%99%BEUSDT" in raw, f"\n{raw}"
        assert "extra=v%E9%BE%99%E8%99%BE" in raw, f"\n{raw}"

    def test_plus_and_equals_pass_through_in_query(self):
        """`+` and `=` are valid in query strings; requests leaves them alone
        and so must we.  Encoding `+` to `%20` would silently corrupt
        callers that pass literal `+` meaning a space."""
        port, joiner = _capture()
        try:
            pyureq.get(
                f"http://127.0.0.1:{port}/x?a=1+2&b=plain",
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner().splitlines()[0]
        assert "a=1+2" in raw, f"\n{raw}"
        assert "b=plain" in raw, f"\n{raw}"