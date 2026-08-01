"""Regression tests for bugs fixed in 0.1.6.

Each test here corresponds to a bug that shipped in 0.1.5 and was not caught by
the existing suite:

  - deflate responses were returned still-compressed (Accept-Encoding lied)
  - Session opened a new TCP connection per request (no pooling)
  - per-request verify= was ignored on the Session path
  - a verify=False connection could be reused for a later verify=True request
"""

import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingTCPServer

import pytest
import pyureq

from .local_server import start_server

PORT = 18893
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = start_server(PORT)
    yield srv
    srv.shutdown()


# ---------------------------------------------------------------------------
# Content-Encoding
# ---------------------------------------------------------------------------


class TestContentEncoding:
    """pyureq must only advertise encodings the Rust core can decode."""

    def test_accept_encoding_excludes_deflate(self):
        # The Rust core (ureq) decodes gzip and br, never deflate.  Advertising
        # deflate made conforming servers return undecodable zlib bytes.
        with pyureq.Session() as s:
            sent = s.get(f"{BASE}/echo-headers").json()
        accept_encoding = sent.get("accept-encoding", "")
        assert "deflate" not in accept_encoding
        assert "gzip" in accept_encoding

    def test_gzip_response_is_decoded(self):
        with pyureq.Session() as s:
            r = s.get(f"{BASE}/encoding/gzip")
        assert r.status_code == 200
        assert r.json() == {"compressed": "gzip"}

    def test_deflate_not_requested_so_body_is_readable(self):
        # The server only deflates when the client offers it.  Since we no
        # longer do, the body must arrive as plain readable JSON rather than
        # raw zlib bytes that blow up .json() with UnicodeDecodeError.
        with pyureq.Session() as s:
            r = s.get(f"{BASE}/encoding/deflate")
        assert r.status_code == 200
        assert r.json() == {"compressed": "deflate"}

    def test_stateless_path_also_decodes(self):
        r = pyureq.get(f"{BASE}/encoding/gzip")
        assert r.json() == {"compressed": "gzip"}


# ---------------------------------------------------------------------------
# Connection pooling
# ---------------------------------------------------------------------------


class _CountingHandler(BaseHTTPRequestHandler):
    """Minimal keep-alive HTTP handler that counts accepted connections."""

    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        type(self).connection_count += 1

    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def counting_server():
    handler = type("Handler", (_CountingHandler,), {"connection_count": 0})
    srv = ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", handler
    srv.shutdown()
    srv.server_close()


class TestConnectionPooling:
    """Session must reuse one connection, as the README promises."""

    def test_session_reuses_one_connection(self, counting_server):
        url, handler = counting_server
        with pyureq.Session() as s:
            for _ in range(5):
                assert s.get(url).status_code == 200
        assert handler.connection_count == 1, (
            f"expected 1 pooled connection, got {handler.connection_count}"
        )

    def test_separate_sessions_do_not_share_a_pool(self, counting_server):
        url, handler = counting_server
        for _ in range(3):
            with pyureq.Session() as s:
                s.get(url)
        assert handler.connection_count == 3


# ---------------------------------------------------------------------------
# Per-request verify
# ---------------------------------------------------------------------------


def _make_self_signed_cert(tmp_path):
    """Write a throwaway self-signed cert+key, or skip if unavailable."""
    crypto = pytest.importorskip(
        "cryptography", reason="cryptography needed to build a self-signed cert"
    )
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    assert crypto  # silence linters

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


class _TLSHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b'{"tls":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def self_signed_server(tmp_path):
    """A local HTTPS server using a self-signed cert no trust store knows."""
    cert_file, key_file = _make_self_signed_cert(tmp_path)
    srv = HTTPServer(("127.0.0.1", 0), _TLSHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # "localhost" so the hostname matches the cert's SAN; only the CA is untrusted.
    yield f"https://localhost:{srv.server_address[1]}/", str(cert_file)
    srv.shutdown()
    srv.server_close()


class TestPerRequestVerify:
    """verify= must be honoured per request, and must not leak across pools."""

    def test_session_honours_per_request_verify_false(self, self_signed_server):
        url, _ = self_signed_server
        with pyureq.Session() as s:
            # Session default is verify=True; the per-request override must win.
            assert s.get(url, verify=False, timeout=10).status_code == 200

    def test_session_default_verify_rejects_self_signed(self, self_signed_server):
        url, _ = self_signed_server
        with pyureq.Session() as s, pytest.raises(pyureq.RequestException):
            s.get(url, timeout=10)

    def test_verify_false_connection_is_not_reused_for_verify_true(
        self, self_signed_server
    ):
        """The security-relevant case.

        ureq keys its connection pool on (scheme, authority, proxy) — TLS
        config is not part of the key.  If both verify modes shared an agent, a
        connection opened with verification disabled would be handed to a later
        verified request, silently skipping certificate validation.
        """
        url, _ = self_signed_server
        with pyureq.Session() as s:
            assert s.get(url, verify=False, timeout=10).status_code == 200
            # Must still fail even though a usable connection is already pooled.
            with pytest.raises(pyureq.RequestException):
                s.get(url, verify=True, timeout=10)
            # And the insecure pool must still work afterwards.
            assert s.get(url, verify=False, timeout=10).status_code == 200

    def test_verify_with_ca_bundle_path(self, self_signed_server):
        """verify="/path/to/ca.pem" trusts exactly that CA."""
        url, cert_path = self_signed_server
        with pyureq.Session() as s:
            assert s.get(url, verify=cert_path, timeout=10).status_code == 200

    def test_stateless_honours_verify_false(self, self_signed_server):
        url, _ = self_signed_server
        assert pyureq.get(url, verify=False, timeout=10).status_code == 200


# ---------------------------------------------------------------------------
# Timeout classification
# ---------------------------------------------------------------------------


class TestTimeoutClassification:
    """Timeouts must map to Timeout, never ConnectionError.

    0.1.5 classified transport errors by matching the OS error string for
    "timed out"/"deadline"; Windows reports WSAETIMEDOUT with neither phrase,
    so timeouts surfaced as ConnectionError.
    """

    @pytest.fixture
    def stalling_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        held = []

        def accept_and_stall():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                held.append(conn)  # accept, then never respond

        threading.Thread(target=accept_and_stall, daemon=True).start()
        yield f"http://127.0.0.1:{srv.getsockname()[1]}/"
        srv.close()
        for c in held:
            c.close()

    def test_read_stall_raises_timeout(self, stalling_server):
        with pytest.raises(pyureq.Timeout):
            pyureq.get(stalling_server, timeout=0.3)

    def test_timeout_is_not_connection_error(self, stalling_server):
        # Timeout must not be conflated with ConnectionError; requests keeps
        # these distinct and callers retry on them differently.
        with pytest.raises(pyureq.Timeout):
            pyureq.get(stalling_server, timeout=0.3)
        try:
            pyureq.get(stalling_server, timeout=0.3)
        except pyureq.Timeout as exc:
            assert not isinstance(exc, pyureq.ConnectionError)

    def test_session_timeout_also_classified(self, stalling_server):
        with pyureq.Session() as s:
            s.timeout = 0.3
            with pytest.raises(pyureq.Timeout):
                s.get(stalling_server)
