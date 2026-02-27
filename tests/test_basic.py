"""Basic functional tests for rupy against a local test server.

Run with:  pytest tests/ -v
"""

import pytest
import rupy

from .local_server import start_server

PORT = 18889
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="session", autouse=True)
def server():
    srv = start_server(PORT)
    yield srv
    srv.shutdown()


class TestGet:
    def test_200(self):
        r = rupy.get(f"{BASE}/get")
        assert r.status_code == 200

    def test_ok_property(self):
        r = rupy.get(f"{BASE}/get")
        assert r.ok is True

    def test_response_repr(self):
        r = rupy.get(f"{BASE}/get")
        assert repr(r) == "<Response [200]>"

    def test_bool_truthy(self):
        r = rupy.get(f"{BASE}/get")
        assert bool(r) is True

    def test_bool_falsy(self):
        r = rupy.get(f"{BASE}/status/404")
        assert bool(r) is False

    def test_url_in_response(self):
        r = rupy.get(f"{BASE}/get")
        assert "127.0.0.1" in r.url

    def test_elapsed_is_timedelta(self):
        from datetime import timedelta
        r = rupy.get(f"{BASE}/get")
        assert isinstance(r.elapsed, timedelta)
        assert r.elapsed.total_seconds() >= 0


class TestQueryParams:
    def test_dict_params(self):
        r = rupy.get(f"{BASE}/get", params={"foo": "bar", "num": "42"})
        assert r.status_code == 200
        args = r.json()["args"]
        assert args["foo"] == "bar"
        assert args["num"] == "42"

    def test_list_of_tuple_params(self):
        r = rupy.get(f"{BASE}/get", params=[("a", "1"), ("b", "2")])
        assert r.status_code == 200
        args = r.json()["args"]
        assert args["a"] == "1"
        assert args["b"] == "2"

    def test_no_params(self):
        r = rupy.get(f"{BASE}/get")
        assert r.json()["args"] == {}


class TestPost:
    def test_json_body(self):
        payload = {"key": "value", "num": 1}
        r = rupy.post(f"{BASE}/post", json=payload)
        assert r.status_code == 200
        assert r.json()["json"] == payload

    def test_form_data(self):
        r = rupy.post(f"{BASE}/post", data={"field": "hello"})
        assert r.status_code == 200
        assert r.json()["form"]["field"] == "hello"

    def test_raw_bytes(self):
        r = rupy.post(f"{BASE}/post", data=b"hello world")
        assert r.status_code == 200
        assert r.json()["data"] == "hello world"

    def test_raw_string(self):
        r = rupy.post(f"{BASE}/post", data="hello")
        assert r.status_code == 200
        assert r.json()["data"] == "hello"


class TestHeaders:
    def test_custom_header_sent(self):
        r = rupy.get(f"{BASE}/headers", headers={"X-Test-Header": "rupy"})
        assert r.status_code == 200
        received = {k.lower(): v for k, v in r.json()["headers"].items()}
        assert received.get("x-test-header") == "rupy"

    def test_response_headers_case_insensitive(self):
        r = rupy.get(f"{BASE}/get")
        ct_lower = r.headers["content-type"]
        ct_upper = r.headers["Content-Type"]
        assert ct_lower == ct_upper

    def test_content_type_header(self):
        r = rupy.get(f"{BASE}/get")
        assert "application/json" in r.headers.get("content-type", "")


class TestResponseContent:
    def test_text_is_str(self):
        r = rupy.get(f"{BASE}/get")
        assert isinstance(r.text, str)

    def test_content_is_bytes(self):
        r = rupy.get(f"{BASE}/get")
        assert isinstance(r.content, bytes)

    def test_json_method(self):
        r = rupy.get(f"{BASE}/json")
        data = r.json()
        assert isinstance(data, dict)
        assert "slideshow" in data


class TestAuth:
    def test_basic_auth_success(self):
        r = rupy.get(f"{BASE}/basic-auth/user/pass", auth=("user", "pass"))
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_basic_auth_failure(self):
        r = rupy.get(f"{BASE}/basic-auth/user/pass", auth=("user", "wrong"))
        assert r.status_code == 401


class TestRedirects:
    def test_follow_redirect(self):
        r = rupy.get(f"{BASE}/redirect/3")
        assert r.status_code == 200

    def test_no_redirect(self):
        r = rupy.get(f"{BASE}/redirect/1", allow_redirects=False)
        assert r.status_code == 302


class TestErrors:
    def test_raise_for_status_4xx(self):
        r = rupy.get(f"{BASE}/status/404")
        with pytest.raises(rupy.HTTPError):
            r.raise_for_status()

    def test_raise_for_status_5xx(self):
        r = rupy.get(f"{BASE}/status/500")
        with pytest.raises(rupy.HTTPError):
            r.raise_for_status()

    def test_raise_for_status_ok(self):
        r = rupy.get(f"{BASE}/get")
        r.raise_for_status()  # should not raise

    def test_timeout(self):
        with pytest.raises(rupy.Timeout):
            rupy.get(f"{BASE}/delay/60", timeout=0.05)


class TestHTTPMethods:
    def test_put(self):
        r = rupy.put(f"{BASE}/put", data={"x": "y"})
        assert r.status_code == 200

    def test_patch(self):
        r = rupy.patch(f"{BASE}/patch", json={"a": 1})
        assert r.status_code == 200
        assert r.json()["json"]["a"] == 1

    def test_delete(self):
        r = rupy.delete(f"{BASE}/delete")
        assert r.status_code == 200

    def test_head(self):
        r = rupy.head(f"{BASE}/get")
        assert r.status_code == 200
        assert r.content == b""


class TestSession:
    def test_context_manager(self):
        with rupy.Session() as s:
            r = s.get(f"{BASE}/get")
            assert r.status_code == 200

    def test_session_default_headers(self):
        with rupy.Session() as s:
            s.headers["X-Session-Header"] = "yes"
            r = s.get(f"{BASE}/headers")
            received = {k.lower(): v for k, v in r.json()["headers"].items()}
            assert received.get("x-session-header") == "yes"

    def test_session_get(self):
        s = rupy.Session()
        r = s.get(f"{BASE}/get")
        assert r.status_code == 200
        s.close()

    def test_session_post_json(self):
        with rupy.Session() as s:
            r = s.post(f"{BASE}/post", json={"session": True})
            assert r.json()["json"]["session"] is True

    def test_session_params(self):
        with rupy.Session() as s:
            s.params = {"session_param": "1"}
            r = s.get(f"{BASE}/get", params={"extra": "2"})
            args = r.json()["args"]
            assert args["session_param"] == "1"
            assert args["extra"] == "2"

    def test_session_auth(self):
        with rupy.Session() as s:
            s.auth = ("user", "pass")
            r = s.get(f"{BASE}/basic-auth/user/pass")
            assert r.status_code == 200
