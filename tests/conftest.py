"""pytest configuration: clear HTTP proxy env vars so local-server tests work."""

import os
import pytest

# reqwest (Rust) reads proxy env vars when building each client.  Strip them
# before any test so localhost connections go directly instead of through the
# network egress proxy that blocks 127.0.0.1.
_PROXY_VARS = [
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
]


@pytest.fixture(scope="session", autouse=True)
def clear_proxy_env():
    saved = {v: os.environ.pop(v, None) for v in _PROXY_VARS}
    yield
    for var, val in saved.items():
        if val is not None:
            os.environ[var] = val
