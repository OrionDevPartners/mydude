"""Tests that the browser redirect & failover protections actually hold.

Two governance guarantees are exercised here, both of which could only be
asserted indirectly before (local Chromium can't launch in this container —
missing libnspr4.so; Browserbase is the prod path):

  1. **Redirect SSRF/TOCTOU block.** An allowed host that 30x-redirects (or
     JS-redirects) to a disallowed / internal host must be aborted *before* the
     hop leaves the browser, with ZERO content captured from the forbidden host.
     This is enforced by the ``page.route`` interceptor in
     ``src/browser/backends.py::_extract_page`` (plus a post-navigation
     belt-and-suspenders check for any hop the interceptor misses).

  2. **No fail-over on a policy block.** A blocked navigation is a policy
     decision, not a backend fault — the engine must NOT retry the forbidden
     navigation on the next available backend (``src/browser/engine.py``). This
     holds for both ``open_page`` and the interactive (login/cancel) loop.

The Playwright page and the remote session are faked, so the suite runs offline
with no credentials and no real browser. One optional live test drives a real
redirect over Browserbase/CDP when BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID
are present; it skips otherwise.

Runnable two ways:
  * ``python tests/test_browser_redirect_failover.py``  (standalone, non-zero exit on failure)
  * ``pytest tests/test_browser_redirect_failover.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.browser.backends import _extract_page
from src.browser.base import BrowserBackend, BrowserBackendSpec, BrowserResult
from src.browser.engine import BrowserEngine
from src.swarm.policy import PolicyEngine


# --------------------------------------------------------------------------- #
# Fake Playwright page that simulates route interception across a redirect chain
# --------------------------------------------------------------------------- #
class _FakeFrame:
    pass


class _FakeRequest:
    def __init__(self, url, frame, navigation=True):
        self.url = url
        self.frame = frame
        self._nav = navigation

    def is_navigation_request(self):
        return self._nav


class _FakeRoute:
    def __init__(self):
        self.aborted = False
        self.continued = False

    async def abort(self, *a, **k):
        self.aborted = True

    async def continue_(self, *a, **k):
        self.continued = True


class _FakePage:
    """Mimics the slice of the Playwright page API ``_extract_page`` touches.

    ``hops`` is the redirect chain: each entry is fed to the registered route
    handler as a navigation request (cheapest faithful model of a 30x / JS
    redirect). If the handler aborts a hop, ``goto`` raises like Playwright does
    on ``net::ERR_ABORTED``. ``final_url`` simulates a hop the interceptor missed
    (to exercise the post-navigation belt-and-suspenders check).

    ``content_calls`` counts every content-extraction call (title/body/shot) so a
    test can assert that ZERO content was captured from a forbidden host.
    """

    def __init__(self, hops, final_url=None):
        self.main_frame = _FakeFrame()
        self.url = None
        self._handler = None
        self._hops = list(hops)
        self._final_url = final_url
        self.content_calls = 0

    async def route(self, pattern, handler):
        self._handler = handler

    async def goto(self, url, timeout=None, wait_until=None):
        for hop in (self._hops or [url]):
            if self._handler is not None:
                route = _FakeRoute()
                req = _FakeRequest(hop, self.main_frame)
                await self._handler(route, req)
                if route.aborted:
                    raise RuntimeError("net::ERR_ABORTED at %s" % hop)
            self.url = hop
        if self._final_url is not None:
            self.url = self._final_url
        return object()

    async def title(self):
        self.content_calls += 1
        return "Internal Admin Console"

    async def inner_text(self, selector):
        self.content_calls += 1
        return "TOP-SECRET INTERNAL CONTENT THAT MUST NEVER BE CAPTURED"

    async def screenshot(self, **kwargs):
        self.content_calls += 1
        return b"\x89PNG-fake-bytes"


_ALLOWED = "example.com"


def _allow_host():
    """Real policy predicate with a known one-domain allow-list."""
    os.environ["BROWSER_ALLOWED_DOMAINS"] = _ALLOWED
    return PolicyEngine().is_host_allowed


# --------------------------------------------------------------------------- #
# 1. Redirect block: allowed host -> 30x -> disallowed/internal host
# --------------------------------------------------------------------------- #
def test_redirect_to_internal_host_is_blocked_before_content():
    """allowed-host -> redirect -> internal host: blocked, zero content."""
    page = _FakePage(hops=[
        "https://example.com/go",       # allowed: interceptor lets it through
        "http://127.0.0.1/admin",       # internal: must be aborted pre-hop
    ])
    res = asyncio.run(_extract_page(
        page, "https://example.com/go", "fake", 5000, True, 4000, _allow_host(),
    ))
    assert res.blocked is True, "redirect to an off-list host must be blocked"
    assert res.ok is False
    assert page.content_calls == 0, (
        "no content may be read from the forbidden host, got %d reads"
        % page.content_calls
    )
    assert res.text in (None, ""), "no page text may be captured on a block"
    assert res.screenshot_b64 is None, "no screenshot may be captured on a block"
    assert "127.0.0.1" in (res.error or "") or res.final_url == "127.0.0.1"


def test_redirect_to_disallowed_public_host_is_blocked():
    """allowed-host -> redirect -> different public host (off-list): blocked."""
    page = _FakePage(hops=[
        "https://example.com/start",
        "https://evil.example.net/steal",
    ])
    res = asyncio.run(_extract_page(
        page, "https://example.com/start", "fake", 5000, True, 4000, _allow_host(),
    ))
    assert res.blocked is True
    assert page.content_calls == 0


def test_missed_redirect_caught_by_post_navigation_check():
    """Belt-and-suspenders: a hop the interceptor misses still lands blocked.

    The route handler only fires for hops it sees; ``final_url`` simulates a
    redirect that resolved without an intercepted navigation request (the gap a
    post-navigation final-host check exists to close). Cloud metadata IP is the
    classic SSRF target.
    """
    page = _FakePage(
        hops=["https://example.com/start"],
        final_url="http://169.254.169.254/latest/meta-data/iam/",
    )
    res = asyncio.run(_extract_page(
        page, "https://example.com/start", "fake", 5000, True, 4000, _allow_host(),
    ))
    assert res.blocked is True, "off-list FINAL host must be blocked even if no hop was intercepted"
    assert page.content_calls == 0
    assert "169.254.169.254" in (res.error or "") or res.final_url and "169.254" in res.final_url


def test_allowed_redirect_chain_succeeds():
    """Control: an in-allow-list redirect chain is NOT blocked and DOES capture."""
    page = _FakePage(hops=[
        "https://example.com/a",
        "https://www.example.com/b",     # subdomain of allowed: permitted
    ])
    res = asyncio.run(_extract_page(
        page, "https://example.com/a", "fake", 5000, True, 4000, _allow_host(),
    ))
    assert res.blocked is False
    assert res.ok is True
    assert page.content_calls > 0, "an allowed page should be read normally"
    assert res.title == "Internal Admin Console"  # fake page's title; proves content captured


# --------------------------------------------------------------------------- #
# 2. No fail-over on a policy block (engine level)
# --------------------------------------------------------------------------- #
class _RecordingBackend(BrowserBackend):
    """Records whether each entry point was invoked, returns a canned result."""

    def __init__(self, key, result):
        super().__init__(BrowserBackendSpec(key=key, adapter="fake"))
        self._result = result
        self.open_calls = 0
        self.login_calls = 0

    def _available(self):
        return True

    async def open_page(self, url, **kwargs):
        self.open_calls += 1
        return self._result

    async def login_page(self, login_url, account_url, username, password, **kwargs):
        self.login_calls += 1
        return self._result


def _blocked_result(key):
    return BrowserResult(
        ok=False, backend=key, blocked=True,
        error="Navigation blocked: a redirect targeted '127.0.0.1', not in allow-list.",
        attempts=[key],
    )


def test_open_page_block_does_not_failover():
    """A blocked open_page must NOT retry the forbidden nav on a 2nd backend."""
    b1 = _RecordingBackend("primary", _blocked_result("primary"))
    b2 = _RecordingBackend("secondary", BrowserResult(ok=True, backend="secondary", text="leaked"))

    engine = BrowserEngine()
    engine.backends = lambda: [b1, b2]  # type: ignore[assignment]

    res = asyncio.run(engine.open_page("https://example.com/go", allow_host=_allow_host()))

    assert res.blocked is True, "the block must be surfaced, not swallowed"
    assert res.ok is False
    assert b1.open_calls == 1, "primary backend should be tried once"
    assert b2.open_calls == 0, (
        "a policy block must NOT fail over to another backend (it would re-attempt "
        "the forbidden navigation), but the secondary was called %d time(s)"
        % b2.open_calls
    )
    assert res.attempts == ["primary"]


def test_interactive_block_does_not_failover():
    """A blocked login flow must NOT fail over either (would re-attempt the nav)."""
    b1 = _RecordingBackend("primary", _blocked_result("primary"))
    b2 = _RecordingBackend("secondary", BrowserResult(ok=True, backend="secondary", text="leaked"))

    engine = BrowserEngine()
    engine.backends = lambda: [b1, b2]  # type: ignore[assignment]

    res = asyncio.run(engine.login_page(
        "https://example.com/login", "https://example.com/account", "user", "pw",
        allow_host=_allow_host(),
    ))

    assert res.blocked is True
    assert b1.login_calls == 1
    assert b2.login_calls == 0, (
        "a blocked interactive flow must NOT fail over, but secondary login was "
        "called %d time(s)" % b2.login_calls
    )
    assert res.attempts == ["primary"]


def test_genuine_backend_failure_does_failover():
    """Control: a real backend ERROR (not a block) SHOULD fail over."""
    b1 = _RecordingBackend("primary", BrowserResult(
        ok=False, backend="primary", error="TimeoutError: nav timed out", attempts=["primary"],
    ))
    b2 = _RecordingBackend("secondary", BrowserResult(
        ok=True, backend="secondary", text="served by secondary",
    ))

    engine = BrowserEngine()
    engine.backends = lambda: [b1, b2]  # type: ignore[assignment]

    res = asyncio.run(engine.open_page("https://example.com/go", allow_host=_allow_host()))

    assert res.ok is True
    assert res.backend == "secondary"
    assert b1.open_calls == 1 and b2.open_calls == 1, (
        "a non-block failure should fail over to the secondary backend"
    )
    assert res.attempts == ["primary", "secondary"]


# --------------------------------------------------------------------------- #
# 3. Optional live Browserbase redirect test (over real CDP)
# --------------------------------------------------------------------------- #
def test_browserbase_live_redirect_blocked():
    """Drive a REAL allowed->disallowed redirect over Browserbase/CDP.

    Uses httpbin's redirector: an allowed host (httpbin.org) 30x-redirects to a
    disallowed host (example.com is removed from the allow-list here). The result
    must come back blocked, proving the interceptor holds over real CDP.

    Skips unless BROWSERBASE_API_KEY + BROWSERBASE_PROJECT_ID are configured.
    """
    if not (os.environ.get("BROWSERBASE_API_KEY") and os.environ.get("BROWSERBASE_PROJECT_ID")):
        _skip("Browserbase credentials not configured (BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID)")
        return

    from src.browser.backends import BrowserbaseBackend

    prev = os.environ.get("BROWSER_ALLOWED_DOMAINS")
    os.environ["BROWSER_ALLOWED_DOMAINS"] = "httpbin.org"  # target host is NOT in the list
    try:
        backend = BrowserbaseBackend(BrowserBackendSpec(
            key="browserbase", adapter="browserbase",
            secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
        ))
        allow_host = PolicyEngine().is_host_allowed
        res = asyncio.run(backend.open_page(
            "https://httpbin.org/redirect-to?url=https%3A%2F%2Fexample.com%2F&status_code=302",
            timeout_ms=45000, screenshot=False, max_chars=2000, allow_host=allow_host,
        ))
        assert res.blocked is True, (
            "live redirect to an off-list host must be blocked over CDP, got: %r"
            % (res.error or res.text)
        )
        assert not (res.text or "").strip(), "no content may be captured from the off-list host"
    finally:
        if prev is None:
            os.environ.pop("BROWSER_ALLOWED_DOMAINS", None)
        else:
            os.environ["BROWSER_ALLOWED_DOMAINS"] = prev


def _skip(reason):
    """Skip under pytest; print + continue when run standalone."""
    try:
        import pytest
        pytest.skip(reason)
    except ImportError:
        print("SKIP test_browserbase_live_redirect_blocked ->", reason)


def _run_all():
    tests = [
        test_redirect_to_internal_host_is_blocked_before_content,
        test_redirect_to_disallowed_public_host_is_blocked,
        test_missed_redirect_caught_by_post_navigation_check,
        test_allowed_redirect_chain_succeeds,
        test_open_page_block_does_not_failover,
        test_interactive_block_does_not_failover,
        test_genuine_backend_failure_does_failover,
        test_browserbase_live_redirect_blocked,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
