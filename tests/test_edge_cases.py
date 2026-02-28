"""Edge-case and gap-filling tests for rupy.

Covers features not exercised by test_basic.py or test_compat.py:
  - verify=False
  - raise_for_status on individual status classes
  - TooManyRedirects exception
  - Timeout exception
  - RequestException for invalid URLs
  - Response.cookies / .reason / .links / .close()
  - Session.verify property (recreates RustClient)
  - rupy.request() generic method
  - rupy.options() HTTP method
  - Edge cases: empty/None params, None cookies
"""

import pytest
import rupy
from rupy.exceptions import (
    HTTPError,
    RequestException,
    Timeout,
    TooManyRedirects,
)

from .local_server import start_server

PORT = 18891
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = start_server(PORT)
    yield srv
    srv.shutdown()


# ---------------------------------------------------------------------------
# verify=False
# ---------------------------------------------------------------------------

class TestVerify:
    def test_verify_false_stateless(self):
        """verify=False should not affect plain HTTP requests."""
        r = rupy.get(f"{BASE}/get", verify=False)
        assert r.status_code == 200

    def test_session_verify_false(self):
        """Session.verify=False should take effect (recreates RustClient)."""
        with rupy.Session() as s:
            assert s.verify is True
            s.verify = False
            assert s.verify is False
            r = s.get(f"{BASE}/get")
            assert r.status_code == 200

    def test_session_verify_toggle(self):
        """Setting Session.verify multiple times should always work."""
        with rupy.Session() as s:
            s.verify = False
            s.verify = True
            r = s.get(f"{BASE}/get")
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# raise_for_status
# ---------------------------------------------------------------------------

class TestRaiseForStatus:
    def test_raises_on_400(self):
        r = rupy.get(f"{BASE}/status/400")
        with pytest.raises(HTTPError) as exc_info:
            r.raise_for_status()
        assert exc_info.value.response.status_code == 400

    def test_raises_on_404(self):
        r = rupy.get(f"{BASE}/status/404")
        with pytest.raises(HTTPError):
            r.raise_for_status()

    def test_raises_on_500(self):
        r = rupy.get(f"{BASE}/status/500")
        with pytest.raises(HTTPError) as exc_info:
            r.raise_for_status()
        assert exc_info.value.response.status_code == 500

    def test_raises_on_503(self):
        r = rupy.get(f"{BASE}/status/503")
        with pytest.raises(HTTPError):
            r.raise_for_status()

    def test_no_raise_on_200(self):
        r = rupy.get(f"{BASE}/status/200")
        r.raise_for_status()  # must not raise

    def test_no_raise_on_201(self):
        r = rupy.post(f"{BASE}/post", json={})
        # server returns 200, just check no raise
        r.raise_for_status()

    def test_error_has_response_attribute(self):
        r = rupy.get(f"{BASE}/status/422")
        with pytest.raises(HTTPError) as exc_info:
            r.raise_for_status()
        assert exc_info.value.response is r


# ---------------------------------------------------------------------------
# TooManyRedirects
# ---------------------------------------------------------------------------

class TestTooManyRedirects:
    def test_too_many_redirects(self):
        """A redirect chain longer than 30 hops should raise TooManyRedirects."""
        with pytest.raises(TooManyRedirects):
            rupy.get(f"{BASE}/redirect/31")

    def test_too_many_redirects_is_request_exception(self):
        with pytest.raises(RequestException):
            rupy.get(f"{BASE}/redirect/31")


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_raises(self):
        with pytest.raises(Timeout):
            rupy.get(f"{BASE}/delay/5", timeout=0.05)

    def test_timeout_is_request_exception(self):
        with pytest.raises(RequestException):
            rupy.get(f"{BASE}/delay/5", timeout=0.05)

    def test_session_timeout(self):
        with rupy.Session() as s:
            s.timeout = 0.05
            with pytest.raises(Timeout):
                s.get(f"{BASE}/delay/5")


# ---------------------------------------------------------------------------
# Bad URLs → RequestException
# ---------------------------------------------------------------------------

class TestBadURLs:
    def test_missing_scheme(self):
        with pytest.raises(RequestException):
            rupy.get("127.0.0.1/get")

    def test_completely_invalid_url(self):
        with pytest.raises(RequestException):
            rupy.get("not_a_url_at_all")

    def test_unknown_scheme(self):
        with pytest.raises(RequestException):
            rupy.get("ftp://example.com/file")


# ---------------------------------------------------------------------------
# Response.reason
# ---------------------------------------------------------------------------

class TestResponseReason:
    def test_reason_200(self):
        r = rupy.get(f"{BASE}/status/200")
        assert r.reason == "OK"

    def test_reason_404(self):
        r = rupy.get(f"{BASE}/status/404")
        assert r.reason == "Not Found"

    def test_reason_500(self):
        r = rupy.get(f"{BASE}/status/500")
        assert r.reason == "Internal Server Error"

    def test_reason_is_string(self):
        r = rupy.get(f"{BASE}/get")
        assert isinstance(r.reason, str)


# ---------------------------------------------------------------------------
# Response.cookies
# ---------------------------------------------------------------------------

class TestResponseCookies:
    def test_cookies_attribute_exists(self):
        r = rupy.get(f"{BASE}/get")
        assert hasattr(r, "cookies")
        assert isinstance(r.cookies, dict)

    def test_cookies_empty_when_none_set(self):
        r = rupy.get(f"{BASE}/get")
        assert r.cookies == {}

    def test_cookies_parsed_from_set_cookie(self):
        r = rupy.get(f"{BASE}/cookies/set", allow_redirects=False)
        assert r.status_code == 200
        # At least one cookie should be present (HashMap may store last Set-Cookie)
        assert len(r.cookies) >= 1

    def test_cookies_are_strings(self):
        r = rupy.get(f"{BASE}/cookies/set", allow_redirects=False)
        for k, v in r.cookies.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


# ---------------------------------------------------------------------------
# Response.close()
# ---------------------------------------------------------------------------

class TestResponseClose:
    def test_close_is_callable(self):
        r = rupy.get(f"{BASE}/get")
        r.close()  # must not raise

    def test_close_returns_none(self):
        r = rupy.get(f"{BASE}/get")
        result = r.close()
        assert result is None

    def test_close_idempotent(self):
        r = rupy.get(f"{BASE}/get")
        r.close()
        r.close()  # second call must not raise


# ---------------------------------------------------------------------------
# Response.links
# ---------------------------------------------------------------------------

class TestResponseLinks:
    def test_links_empty_when_no_header(self):
        r = rupy.get(f"{BASE}/get")
        assert r.links == {}

    def test_links_parsed_from_link_header(self):
        link_val = '<http://127.0.0.1:{}/get>; rel="next"'.format(PORT)
        r = rupy.get(
            f"{BASE}/response-headers",
            params={"Link": link_val},
        )
        links = r.links
        assert "next" in links
        assert "url" in links["next"]

    def test_links_is_dict(self):
        r = rupy.get(f"{BASE}/get")
        assert isinstance(r.links, dict)


# ---------------------------------------------------------------------------
# rupy.request() generic method
# ---------------------------------------------------------------------------

class TestGenericRequest:
    def test_request_get(self):
        r = rupy.request("GET", f"{BASE}/get")
        assert r.status_code == 200

    def test_request_post(self):
        r = rupy.request("POST", f"{BASE}/post", json={"x": 1})
        assert r.status_code == 200
        assert r.json()["json"] == {"x": 1}

    def test_request_case_insensitive_method(self):
        r = rupy.request("get", f"{BASE}/get")
        assert r.status_code == 200

    def test_request_delete(self):
        r = rupy.request("DELETE", f"{BASE}/delete")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# rupy.options() HTTP method
# ---------------------------------------------------------------------------

class TestOptions:
    def test_options_method(self):
        r = rupy.options(f"{BASE}/options")
        assert r.status_code == 200

    def test_options_via_request(self):
        r = rupy.request("OPTIONS", f"{BASE}/options")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Params / cookies edge cases
# ---------------------------------------------------------------------------

class TestEdgeCaseParams:
    def test_none_params(self):
        r = rupy.get(f"{BASE}/get", params=None)
        assert r.status_code == 200

    def test_empty_dict_params(self):
        r = rupy.get(f"{BASE}/get", params={})
        assert r.status_code == 200

    def test_none_cookies(self):
        r = rupy.get(f"{BASE}/get", cookies=None)
        assert r.status_code == 200

    def test_empty_dict_cookies(self):
        r = rupy.get(f"{BASE}/get", cookies={})
        assert r.status_code == 200

    def test_none_headers(self):
        r = rupy.get(f"{BASE}/get", headers=None)
        assert r.status_code == 200

    def test_list_of_tuple_params(self):
        r = rupy.get(f"{BASE}/get", params=[("a", "1"), ("a", "2")])
        assert r.status_code == 200
        data = r.json()
        # Both values should be present
        assert "a" in data["args"]
