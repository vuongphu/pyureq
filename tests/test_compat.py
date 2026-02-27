"""Backward-compatibility tests.

Verifies that code written for ``requests`` runs without modification
when ``import rupy as requests`` is used instead.
"""

import pytest

from .local_server import start_server

PORT = 18890
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = start_server(PORT)
    yield srv
    srv.shutdown()


def get_client():
    """Return ``rupy`` imported as ``requests``."""
    import rupy as requests
    return requests


class TestDropInReplacement:
    """Every test replicates a common real-world requests usage pattern."""

    def test_alias_import(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert r.status_code == 200

    def test_response_text(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert isinstance(r.text, str)
        assert len(r.text) > 0

    def test_response_content(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert isinstance(r.content, bytes)

    def test_response_json(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        data = r.json()
        assert isinstance(data, dict)
        assert "url" in data

    def test_response_headers_dict_like(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        ct = r.headers.get("Content-Type")
        assert ct is not None
        # Case-insensitive
        assert r.headers.get("content-type") == ct

    def test_post_with_json(self):
        requests = get_client()
        payload = {"hello": "world", "number": 42}
        r = requests.post(f"{BASE}/post", json=payload)
        assert r.status_code == 200
        assert r.json()["json"] == payload

    def test_post_with_form_data(self):
        requests = get_client()
        r = requests.post(f"{BASE}/post", data={"name": "alice", "age": "30"})
        assert r.status_code == 200
        form = r.json()["form"]
        assert form["name"] == "alice"
        assert form["age"] == "30"

    def test_custom_headers(self):
        requests = get_client()
        r = requests.get(
            f"{BASE}/headers",
            headers={"X-My-Header": "compat-test"},
        )
        received = {k.lower(): v for k, v in r.json()["headers"].items()}
        assert received.get("x-my-header") == "compat-test"

    def test_query_params(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get", params={"page": "2", "limit": "50"})
        args = r.json()["args"]
        assert args["page"] == "2"
        assert args["limit"] == "50"

    def test_basic_auth(self):
        requests = get_client()
        r = requests.get(
            f"{BASE}/basic-auth/admin/secret",
            auth=("admin", "secret"),
        )
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_raise_for_status(self):
        requests = get_client()
        r = requests.get(f"{BASE}/status/404")
        with pytest.raises(requests.HTTPError):
            r.raise_for_status()

    def test_ok_attribute(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert r.ok is True

    def test_put_request(self):
        requests = get_client()
        r = requests.put(f"{BASE}/put", json={"updated": True})
        assert r.status_code == 200

    def test_delete_request(self):
        requests = get_client()
        r = requests.delete(f"{BASE}/delete")
        assert r.status_code == 200

    def test_head_request(self):
        requests = get_client()
        r = requests.head(f"{BASE}/get")
        assert r.status_code == 200

    def test_session_usage(self):
        """Typical Session-based code pattern."""
        requests = get_client()
        with requests.Session() as s:
            s.headers.update({"X-App-Version": "1.0"})

            r = s.get(f"{BASE}/headers")
            assert r.status_code == 200
            received = {k.lower(): v for k, v in r.json()["headers"].items()}
            assert received.get("x-app-version") == "1.0"

    def test_session_request_method(self):
        requests = get_client()
        with requests.Session() as s:
            r = s.request("GET", f"{BASE}/get")
            assert r.status_code == 200

    def test_response_bool_truthy(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert bool(r) is True

    def test_response_url(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert r.url.startswith("http")

    def test_timeout_raises(self):
        requests = get_client()
        with pytest.raises(requests.Timeout):
            requests.get(f"{BASE}/delay/60", timeout=0.05)

    def test_patch_request(self):
        requests = get_client()
        r = requests.patch(f"{BASE}/patch", json={"patched": True})
        assert r.status_code == 200
        assert r.json()["json"]["patched"] is True

    def test_exception_hierarchy(self):
        """Verify our exception hierarchy is compatible with requests'."""
        requests = get_client()
        # HTTPError is a RequestException
        assert issubclass(requests.HTTPError, requests.RequestException)
        # ConnectionError is a RequestException
        assert issubclass(requests.ConnectionError, requests.RequestException)
        # Timeout is a RequestException
        assert issubclass(requests.Timeout, requests.RequestException)
        # ConnectTimeout is both ConnectionError and Timeout
        assert issubclass(requests.ConnectTimeout, requests.ConnectionError)
        assert issubclass(requests.ConnectTimeout, requests.Timeout)

    def test_headers_in_is_operator(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert "content-type" in r.headers
        assert "Content-Type" in r.headers
        assert "CONTENT-TYPE" in r.headers

    def test_response_encoding_default(self):
        requests = get_client()
        r = requests.get(f"{BASE}/get")
        assert isinstance(r.encoding, str)
        assert len(r.encoding) > 0


class TestExistingCodePattern:
    """Simulate realistic application code written for requests."""

    def test_api_client_pattern(self):
        """Typical API client class pattern."""
        import rupy as requests

        class APIClient:
            def __init__(self, api_key, base_url):
                self.session = requests.Session()
                self.session.headers["Authorization"] = f"Bearer {api_key}"
                self.session.headers["Accept"] = "application/json"
                self.base_url = base_url

            def get_resource(self, path, **params):
                r = self.session.get(f"{self.base_url}{path}", params=params)
                r.raise_for_status()
                return r.json()

            def create_resource(self, path, data):
                r = self.session.post(f"{self.base_url}{path}", json=data)
                r.raise_for_status()
                return r.json()

            def close(self):
                self.session.close()

        client = APIClient(api_key="test-key-123", base_url=BASE)
        try:
            result = client.get_resource("/get", extra="param")
            assert isinstance(result, dict)

            created = client.create_resource("/post", {"name": "test"})
            assert isinstance(created, dict)
            assert created["json"]["name"] == "test"
        finally:
            client.close()

    def test_response_chaining_pattern(self):
        """Common: resp.raise_for_status() then .json()."""
        import rupy as requests

        def fetch_data(url):
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()

        data = fetch_data(f"{BASE}/json")
        assert isinstance(data, dict)
        assert "slideshow" in data

    def test_context_manager_pattern(self):
        """using Session as context manager."""
        import rupy as requests

        results = []
        with requests.Session() as session:
            for endpoint in ["/get", "/json"]:
                r = session.get(f"{BASE}{endpoint}")
                results.append(r.status_code)

        assert all(code == 200 for code in results)
