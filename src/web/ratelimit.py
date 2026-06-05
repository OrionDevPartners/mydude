"""In-process abuse/cost controls for the web app.

Single-process, in-memory primitives:
- ``RateLimiter``: thread-safe fixed-window counter keyed by an arbitrary
  string (e.g. client IP). Used for login abuse protection and to throttle the
  expensive LLM fan-out endpoint.
- ``ConcurrencyGuard``: async semaphore-style guard that bounds how many costly
  requests run at once and rejects (rather than queues) once full.
- ``client_ip``: best-effort caller IP, honoring the proxy ``X-Forwarded-For``
  header used by the Replit preview/deployment edge.

These are intentionally process-local. They are a pragmatic guardrail for a
single-instance app, not a distributed rate limiter.
"""
import time
import threading
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import Request


class RateLimiter:
    """Thread-safe fixed-window rate limiter."""

    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window = window_seconds
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, dq: Deque[float], now: float) -> None:
        while dq and (now - dq[0]) > self.window:
            dq.popleft()

    def check(self, key: str) -> Tuple[bool, int]:
        """Record an event for ``key``.

        Returns ``(allowed, retry_after_seconds)``. When not allowed the event
        is NOT recorded so a blocked caller cannot keep the window pinned open.
        """
        now = time.time()
        with self._lock:
            dq = self._events[key]
            self._prune(dq, now)
            if len(dq) >= self.max_events:
                retry_after = int(self.window - (now - dq[0])) + 1
                return False, max(1, retry_after)
            dq.append(now)
            return True, 0

    def peek(self, key: str) -> Tuple[bool, int]:
        """Like :meth:`check` but never records an event (read-only test)."""
        now = time.time()
        with self._lock:
            dq = self._events[key]
            self._prune(dq, now)
            if len(dq) >= self.max_events:
                retry_after = int(self.window - (now - dq[0])) + 1
                return False, max(1, retry_after)
            return True, 0

    def record(self, key: str) -> None:
        """Record an event unconditionally (used to count failures)."""
        now = time.time()
        with self._lock:
            dq = self._events[key]
            self._prune(dq, now)
            dq.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class ConcurrencyGuard:
    """Bounds concurrent costly operations; rejects immediately when full.

    Uses a plain counter under a lock rather than a blocking semaphore so a
    caller is rejected (HTTP 429) instead of silently queueing behind expensive
    in-flight work.
    """

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.max_concurrent:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1


def client_ip(request: Request) -> str:
    """Best-effort client IP, honoring the proxy forwarded-for header."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First entry is the original client.
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
