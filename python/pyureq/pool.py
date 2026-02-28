"""Thread-safe pyureq session pool with hard-timeout protection.

Each worker thread owns a persistent :class:`pyureq.Session` (one Rust
connection pool per thread).  An outer ``ThreadPoolExecutor`` enforces a
hard wall-clock deadline on top of pyureq's built-in ureq timeout, guarding
against edge cases where the underlying I/O does not honour the requested
timeout.

Because pyureq releases the GIL for all network I/O (via
``py.allow_threads`` in Rust), worker threads run truly in parallel —
this is not a workaround for the GIL; it is genuine concurrency.

Typical usage::

    from pyureq.pool import SessionPool

    pool = SessionPool(max_concurrent=20, timeout=10)
    with pool:
        resp = pool.get("https://example.com")
        print(resp.status_code)
"""

import atexit
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional

from .exceptions import Timeout
from .sessions import Session

logger = logging.getLogger(__name__)


class SessionPool:
    """Thread-safe pyureq session pool with hard-timeout protection.

    Each worker thread gets its own :class:`~pyureq.Session` so that
    connection pools are never shared across threads.  An extra
    ``future.result(timeout=timeout + 5)`` deadline is imposed on every
    request as a final safety net in case ureq's internal timeout misfires.

    Args:
        max_concurrent: Maximum number of concurrent worker threads.
        timeout: Default request timeout in seconds.
        verify: Whether to verify TLS certificates.
        **session_kwargs: Additional attributes forwarded to each
            :class:`~pyureq.Session` (e.g. ``auth``, ``headers``).
    """

    def __init__(
        self,
        max_concurrent: int = 30,
        timeout: float = 30.0,
        verify: bool = True,
        **session_kwargs,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.default_timeout = timeout
        self.verify = verify
        self._session_kwargs = session_kwargs

        # Thread-local pyureq sessions — one Rust connection pool per worker
        self._thread_local = threading.local()

        # Registry of every session ever created so close() can clean them all
        self._sessions_lock = threading.Lock()
        self._all_sessions: list = []

        # Stats counters
        self._lock = threading.Lock()
        self._request_count = 0
        self._error_count = 0
        self._timeout_count = 0
        self._closed = False

        # Executor and recycling state
        self._executor_lock = threading.Lock()
        self._executor = self._new_executor()
        self._executor_created_at = time.monotonic()
        self._last_request_time = time.monotonic()
        # Recycle executor when idle >5 min or alive >30 min
        # (ThreadPoolExecutor never terminates idle workers on its own)
        self._executor_idle_timeout = 300
        self._executor_max_age = 1800

        atexit.register(self.close)
        logger.info(
            "pyureq SessionPool initialised (%d workers, timeout=%.1fs)",
            max_concurrent,
            timeout,
        )

    # ------------------------------------------------------------------
    # Executor lifecycle
    # ------------------------------------------------------------------

    def _new_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self.max_concurrent,
            thread_name_prefix="pyureq-worker",
        )

    def _get_executor(self) -> ThreadPoolExecutor:
        """Return a stable reference to the current executor under lock."""
        with self._executor_lock:
            return self._executor

    def _maybe_recycle_executor(self) -> None:
        """Recycle the executor when idle or aged out.

        ThreadPoolExecutor keeps idle worker threads alive indefinitely.
        Recycling is the only way to release them.  The outer check is
        intentionally cheap so it can run before every request.
        """
        now = time.monotonic()
        if (
            now - self._last_request_time < self._executor_idle_timeout
            and now - self._executor_created_at < self._executor_max_age
        ):
            return

        with self._executor_lock:
            # Double-checked locking — re-evaluate after acquiring the lock
            now = time.monotonic()
            if (
                now - self._last_request_time < self._executor_idle_timeout
                and now - self._executor_created_at < self._executor_max_age
            ):
                return

            old = self._executor
            self._executor = self._new_executor()
            self._executor_created_at = now

        # Shut down the old executor outside the lock so we don't block callers
        def _shutdown_old() -> None:
            try:
                old.shutdown(wait=True, cancel_futures=True)
            except TypeError:
                old.shutdown(wait=False)

        threading.Thread(
            target=_shutdown_old, daemon=True, name="pyureq-pool-cleanup"
        ).start()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _get_session(self) -> Session:
        """Return (or lazily create) the pyureq Session for this thread."""
        if (
            not hasattr(self._thread_local, "session")
            or self._thread_local.session is None
        ):
            s = Session()
            s.verify = self.verify
            if self.default_timeout is not None:
                s.timeout = self.default_timeout
            for attr, val in self._session_kwargs.items():
                setattr(s, attr, val)

            self._thread_local.session = s
            self._thread_local.created_at = time.monotonic()
            self._thread_local.request_count = 0

            with self._sessions_lock:
                self._all_sessions.append(s)

        return self._thread_local.session

    def _should_recreate_session(self) -> bool:
        if not hasattr(self._thread_local, "created_at"):
            return False
        age = time.monotonic() - self._thread_local.created_at
        count = getattr(self._thread_local, "request_count", 0)
        return age > 1800 or count > 1000

    def _recreate_session(self) -> None:
        old = getattr(self._thread_local, "session", None)
        self._thread_local.session = None
        if old is not None:
            with self._sessions_lock:
                try:
                    self._all_sessions.remove(old)
                except ValueError:
                    pass
            try:
                old.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal request (runs inside a worker thread)
    # ------------------------------------------------------------------

    def _make_request_internal(
        self, method: str, url: str, timeout: float, **kwargs
    ):
        if self._should_recreate_session():
            self._recreate_session()

        session = self._get_session()
        response = session.request(method, url, timeout=timeout, **kwargs)

        if hasattr(self._thread_local, "request_count"):
            self._thread_local.request_count += 1

        return response

    # ------------------------------------------------------------------
    # Public request API
    # ------------------------------------------------------------------

    def request(
        self, method: str, url: str, timeout: Optional[float] = None, **kwargs
    ):
        """Make an HTTP request with a hard-timeout safety net.

        pyureq enforces *timeout* inside Rust (ureq).  This method adds an
        outer ``future.result(timeout + 5)`` deadline so that stuck requests
        are abandoned even if the Rust timeout misfires.

        Raises:
            RuntimeError: If the pool has been closed.
            pyureq.Timeout: If the hard deadline expires.
        """
        if self._closed:
            raise RuntimeError(
                "SessionPool is closed; call reopen() to create a new one"
            )

        # Use `is None` so that timeout=0 (no timeout) is honoured correctly
        if timeout is None:
            timeout = self.default_timeout

        self._maybe_recycle_executor()
        self._last_request_time = time.monotonic()

        # Capture executor reference under lock to avoid a race with recycling
        executor = self._get_executor()
        future = executor.submit(
            self._make_request_internal, method, url, timeout, **kwargs
        )

        try:
            result = future.result(timeout=timeout + 5)
            with self._lock:
                self._request_count += 1
            return result

        except FutureTimeoutError:
            with self._lock:
                self._timeout_count += 1
            logger.warning(
                "Request to %s timed out after %.1fs (hard limit)", url, timeout + 5
            )
            # cancel() is a no-op for running tasks but frees queued ones
            future.cancel()
            raise Timeout(f"Request timed out after {timeout + 5:.1f}s")

        except Exception:
            with self._lock:
                self._error_count += 1
            raise

    def get(self, url: str, **kwargs):
        """Sends a GET request."""
        kwargs.setdefault("allow_redirects", True)
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data=None, json=None, **kwargs):
        """Sends a POST request."""
        return self.request("POST", url, data=data, json=json, **kwargs)

    def put(self, url: str, data=None, **kwargs):
        """Sends a PUT request."""
        return self.request("PUT", url, data=data, **kwargs)

    def patch(self, url: str, data=None, **kwargs):
        """Sends a PATCH request."""
        return self.request("PATCH", url, data=data, **kwargs)

    def delete(self, url: str, **kwargs):
        """Sends a DELETE request."""
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs):
        """Sends a HEAD request."""
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs):
        """Sends an OPTIONS request."""
        kwargs.setdefault("allow_redirects", True)
        return self.request("OPTIONS", url, **kwargs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Shut down the executor and close all thread-local sessions."""
        if self._closed:
            return
        self._closed = True

        # Collect and clear the session registry atomically
        with self._sessions_lock:
            sessions, self._all_sessions = list(self._all_sessions), []

        for s in sessions:
            try:
                s.close()
            except Exception:
                pass

        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)

        atexit.unregister(self.close)

    def reopen(self) -> None:
        """Re-open the pool after :meth:`close`."""
        with self._executor_lock:
            if not self._closed:
                return
            self._closed = False
            self._executor = self._new_executor()
            self._executor_created_at = time.monotonic()
            self._thread_local = threading.local()
            atexit.register(self.close)

    def get_stats(self) -> dict:
        """Return a snapshot of request statistics."""
        with self._lock:
            total = self._request_count
            errors = self._error_count
            timeouts = self._timeout_count
            return {
                "total_requests": total,
                "errors": errors,
                "timeouts": timeouts,
                "error_rate": errors / max(1, total),
                "timeout_rate": timeouts / max(1, total),
                "closed": self._closed,
            }

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        state = "closed" if self._closed else f"{self.max_concurrent} workers"
        return f"<pyureq.SessionPool [{state}]>"


# ---------------------------------------------------------------------------
# Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import concurrent.futures

    logging.basicConfig(level=logging.INFO)

    pool = SessionPool(max_concurrent=20, timeout=10)

    def make_request(i: int) -> str:
        try:
            resp = pool.get("https://httpbin.org/delay/1")
            return f"Request {i}: {resp.status_code}"
        except Exception as exc:
            return f"Request {i}: Error — {exc}"

    print("Sending 50 requests (20 concurrent) via pyureq SessionPool …")

    # A single ThreadPoolExecutor is enough — SessionPool manages its own
    # workers internally.  No double-wrapping needed.
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as outer:
        results = list(outer.map(make_request, range(50)))

    success = sum(1 for r in results if "Error" not in r)
    errors = len(results) - success
    print(f"\nResults: {success} success, {errors} errors")
    print(f"Stats:   {pool.get_stats()}")

    pool.close()
    print("Done.")
