"""Smoke tests for the interactive browser path (login / cancel).

These guard against a regression that shipped once: the production backend
(Browserbase) only implemented ``open_page`` and inherited the base "does not
support interactive login" defaults, which silently made subscription login and
cancellation non-functional in deployment.

The tests assert that:
  1. ``BrowserbaseBackend`` actually OVERRIDES ``login_page``/``cancel_action``
     (it is not using the unsupported base defaults).
  2. Driving ``login_page``/``cancel_action`` over a (faked) remote session
     reaches the real interactive flow and never returns a "does not support"
     error — both directly on the backend and through ``BrowserEngine``.

The remote Browserbase session and the page-level Playwright interactions are
faked, so the tests run offline with no credentials and no real browser.

Runnable two ways:
  * ``python tests/test_browser_interactive.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_browser_interactive.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.browser import backends as backends_mod
from src.browser.base import BrowserBackend, BrowserBackendSpec, BrowserResult
from src.browser.backends import BrowserbaseBackend
from src.browser.engine import BrowserEngine


class _FakeClosable:
    async def close(self):
        return None

    async def stop(self):
        return None


def _make_backend():
    spec = BrowserBackendSpec(
        key="browserbase",
        adapter="browserbase",
        secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
        label="Browserbase",
    )
    backend = BrowserbaseBackend(spec)

    async def _fake_connect():
        # (pw, browser, page, cleanup) — page is never touched because we patch
        # the module-level _do_login/_do_cancel that would drive it. cleanup is
        # None (Browserbase ends its session when the CDP connection closes).
        return _FakeClosable(), _FakeClosable(), object(), None

    backend._connect = _fake_connect  # type: ignore[assignment]
    return backend


def test_browserbase_overrides_interactive_methods():
    """Production backend must not fall back to the unsupported base defaults."""
    assert BrowserbaseBackend.login_page is not BrowserBackend.login_page, (
        "BrowserbaseBackend.login_page must be overridden (it isn't)."
    )
    assert BrowserbaseBackend.cancel_action is not BrowserBackend.cancel_action, (
        "BrowserbaseBackend.cancel_action must be overridden (it isn't)."
    )


def test_browserbase_login_page_reaches_interactive_flow(monkeypatch=None):
    backend = _make_backend()

    async def _fake_do_login(page, *a, **k):
        # Honest "needs you" result — the realistic offline outcome.
        return BrowserResult(ok=False, backend="browserbase",
                             error="A CAPTCHA was presented — please finish login.")

    backends_mod._do_login = _fake_do_login  # patched for the duration of the run

    res = asyncio.run(backend.login_page(
        "https://example.com/login", "https://example.com/account",
        "user", "pw",
    ))
    assert isinstance(res, BrowserResult)
    assert res.error and "does not support" not in res.error, (
        "login_page must reach the real interactive flow, got: %r" % res.error
    )


def test_browserbase_cancel_action_reaches_interactive_flow():
    backend = _make_backend()

    async def _fake_do_login(page, *a, **k):
        return None  # login "succeeded"

    async def _fake_do_cancel(page, *a, **k):
        return BrowserResult(ok=True, backend="browserbase", text="Clicked: Cancel")

    backends_mod._do_login = _fake_do_login
    backends_mod._do_cancel = _fake_do_cancel

    res = asyncio.run(backend.cancel_action(
        "https://example.com/login", "https://example.com/account",
        "user", "pw",
    ))
    assert isinstance(res, BrowserResult)
    assert res.ok and (not res.error or "does not support" not in res.error), (
        "cancel_action must reach the real cancel flow, got: %r" % (res.error or res.text)
    )


def test_engine_routes_interactive_to_browserbase():
    """The engine's failover loop must dispatch to the Browserbase override."""
    backend = _make_backend()

    async def _fake_do_login(page, *a, **k):
        return BrowserResult(ok=False, backend="browserbase",
                             error="The site asked for a one-time code.")

    backends_mod._do_login = _fake_do_login

    engine = BrowserEngine()
    engine.backends = lambda: [backend]            # type: ignore[assignment]
    backend.available = lambda: True               # type: ignore[assignment]

    res = asyncio.run(engine.login_page(
        "https://example.com/login", "https://example.com/account", "user", "pw",
    ))
    assert isinstance(res, BrowserResult)
    assert res.error and "does not support" not in res.error, (
        "engine.login_page must use the Browserbase override, got: %r" % res.error
    )
    assert "browserbase" in res.attempts


def _run_all():
    tests = [
        test_browserbase_overrides_interactive_methods,
        test_browserbase_login_page_reaches_interactive_flow,
        test_browserbase_cancel_action_reaches_interactive_flow,
        test_engine_routes_interactive_to_browserbase,
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
