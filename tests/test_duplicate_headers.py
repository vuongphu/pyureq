"""Regression tests for duplicate request headers (fixed after 0.1.6).

`http::request::Builder::header` APPENDS rather than replaces, so any header
the Rust core derives itself was emitted a second time when the caller also
passed it explicitly:

  - Content-Type, derived from ``json=`` / ``data=``
  - Cookie, derived from ``cookies=``
  - Authorization, derived from ``auth=``

This shipped in 0.1.6 as part of the ureq 2 -> ureq 3 migration; ureq 2's
``Request::set`` replaced, ureq 3's builder appends.

The failure is nasty because most servers tolerate a repeated header, so it
stays invisible until you hit a strict gateway. Crypto.com's exchange API
answers a duplicated Content-Type with ``{"code": 50001, "message":
"ERR_INTERNAL"}`` -- a generic error that points nowhere near the real cause.

These tests read the raw request bytes off a socket. A WSGI/Flask server
collapses repeated headers into a single mapping entry, so it cannot observe
the bug at all -- do not "simplify" these onto the shared local_server.
"""

import socket
import threading

import pytest
import pyureq


def _capture(handler_response=b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'):
    """Start a one-shot raw TCP server; return (port, joiner).

    ``joiner()`` blocks until the request is read and returns the raw header
    block as a string.
    """
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
                conn.sendall(handler_response)
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


def _count(raw: str, header: str) -> int:
    """How many times `header` appears in the raw request block."""
    prefix = header.lower() + ":"
    return sum(1 for line in raw.split("\r\n") if line.lower().startswith(prefix))


class TestNoDuplicateHeaders:
    """A caller-supplied header must never be sent alongside a derived one."""

    @pytest.mark.parametrize(
        "header_name",
        ["Content-Type", "content-type", "CONTENT-TYPE"],
        ids=["titlecase", "lowercase", "uppercase"],
    )
    def test_content_type_not_duplicated(self, header_name):
        """The 50001 bug. Header names are case-insensitive per RFC 9110."""
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={header_name: "application/json"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Content-Type") == 1, (
            f"Content-Type sent {_count(raw, 'Content-Type')}x with "
            f"header key {header_name!r}:\n{raw}"
        )

    def test_cookie_not_duplicated(self):
        """Explicit Cookie header + cookies= kwarg."""
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={"Cookie": "a=1"},
                cookies={"b": "2"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Cookie") == 1, f"Cookie duplicated:\n{raw}"

    def test_authorization_not_duplicated(self):
        """Explicit Authorization header + auth= kwarg."""
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={"Authorization": "Bearer TOKEN"},
                auth=("user", "pass"),
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Authorization") == 1, f"Authorization duplicated:\n{raw}"


class TestCallerHeaderWins:
    """When both are present the caller's value must survive, like requests."""

    def test_explicit_content_type_is_the_one_sent(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={"Content-Type": "application/vnd.api+json"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert "application/vnd.api+json" in raw, (
            f"caller's Content-Type was discarded:\n{raw}"
        )
        assert _count(raw, "Content-Type") == 1

    def test_explicit_authorization_beats_auth_kwarg(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={"Authorization": "Bearer TOKEN"},
                auth=("user", "pass"),
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert "Bearer TOKEN" in raw, f"caller's Authorization was discarded:\n{raw}"
        assert "Basic " not in raw, f"auth= overrode the explicit header:\n{raw}"


class TestDerivedHeadersStillWork:
    """The fix must not stop pyureq deriving headers when none was given."""

    def test_json_still_sets_content_type(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x", json={"a": 1}, timeout=8, proxies={}
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Content-Type") == 1
        assert "application/json" in raw, f"json= did not set Content-Type:\n{raw}"

    def test_form_data_still_sets_content_type(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x", data={"a": "1"}, timeout=8, proxies={}
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Content-Type") == 1
        assert "application/x-www-form-urlencoded" in raw, (
            f"data= dict did not set Content-Type:\n{raw}"
        )

    def test_cookies_kwarg_still_sets_cookie(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                cookies={"b": "2"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Cookie") == 1
        assert "b=2" in raw, f"cookies= did not set Cookie:\n{raw}"

    def test_auth_kwarg_still_sets_authorization(self):
        port, joiner = _capture()
        try:
            pyureq.Session().post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                auth=("user", "pass"),
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Authorization") == 1
        assert "Basic " in raw, f"auth= did not set Authorization:\n{raw}"


class TestModuleLevelApiToo:
    """The one-shot path shares http_execute, but pin it independently."""

    def test_module_post_does_not_duplicate(self):
        port, joiner = _capture()
        try:
            pyureq.post(
                f"http://127.0.0.1:{port}/x",
                json={"a": 1},
                headers={"Content-Type": "application/json"},
                timeout=8,
                proxies={},
            )
        except Exception:
            pass
        raw = joiner()
        assert _count(raw, "Content-Type") == 1, f"module-level post duplicated:\n{raw}"
