"""Tests that the Apify backend captures and returns a page screenshot.

The Apify Actor runs in Apify's cloud, so there is no local browser or CDP
session. These tests fake the single REST call (``requests.post`` to the
run-sync-get-dataset-items endpoint) so the suite runs offline with no Apify
token and no network. They assert four things:

  1. When a screenshot is requested, the backend asks the Actor for one
     (``customData.screenshot`` is set) and surfaces the base64 image the Actor
     returns in ``screenshot_b64``.
  2. The screenshot is gated by the redirect allow-list exactly like the page
     text — a run that lands off-list comes back ``blocked`` with NO screenshot.
  3. Capture failing inside the Actor (null screenshot) degrades gracefully:
     ``open_page`` still succeeds with ``screenshot_b64=None``.
  4. ``screenshot=False`` neither requests nor returns an image.

Runnable two ways:
  * ``python tests/test_apify_screenshot.py``  (standalone, non-zero exit on failure)
  * ``pytest tests/test_apify_screenshot.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.browser.backends as backends
from src.browser.backends import ApifyBackend
from src.browser.base import BrowserBackendSpec
from src.swarm.policy import PolicyEngine


_ALLOWED = "example.com"
_FAKE_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def _allow_host():
    """Real policy predicate with a known one-domain allow-list."""
    os.environ["BROWSER_ALLOWED_DOMAINS"] = _ALLOWED
    return PolicyEngine().is_host_allowed


class _FakeResponse:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return self._items


def _patch_requests(monkeypatchable_item, capture=None):
    """Replace ``requests.post`` used inside ``_run_actor`` with a fake.

    ``capture`` (when a dict) records the JSON body the backend posted, so a
    test can assert the screenshot flag was actually requested.
    """
    import requests

    orig = requests.post  # noqa: F841 (returned to caller for restore)

    def fake_post(url, headers=None, json=None, timeout=None):
        if capture is not None:
            capture["body"] = json
        return _FakeResponse([monkeypatchable_item])

    requests.post = fake_post
    return orig


def _backend():
    return ApifyBackend(BrowserBackendSpec(
        key="apify", adapter="apify", secrets=["APIFY_API_TOKEN"],
    ))


def _with_token(fn):
    prev = os.environ.get("APIFY_API_TOKEN")
    os.environ["APIFY_API_TOKEN"] = "fake-token"
    try:
        return fn()
    finally:
        if prev is None:
            os.environ.pop("APIFY_API_TOKEN", None)
        else:
            os.environ["APIFY_API_TOKEN"] = prev


def test_screenshot_requested_and_returned():
    """When asked for a screenshot, the Actor is told to capture one and the
    base64 image it returns is surfaced in ``screenshot_b64``."""
    item = {
        "url": "https://example.com/p",
        "finalUrl": "https://example.com/p",
        "title": "Hello",
        "text": "body text",
        "screenshot": _FAKE_PNG_B64,
    }
    capture = {}
    import requests
    orig = _patch_requests(item, capture)
    try:
        res = _with_token(lambda: asyncio.run(_backend().open_page(
            "https://example.com/p", screenshot=True, allow_host=_allow_host(),
        )))
    finally:
        requests.post = orig

    assert res.ok is True
    assert res.screenshot_b64 == _FAKE_PNG_B64, "the Actor's screenshot must be surfaced"
    assert capture["body"]["customData"]["screenshot"] is True, (
        "open_page(screenshot=True) must ask the Actor to capture one"
    )
    assert "page.screenshot" in capture["body"]["pageFunction"], (
        "the injected pageFunction must call page.screenshot"
    )


def test_screenshot_gated_by_allow_list_on_redirect():
    """A run that lands off-list is blocked with NO screenshot leaked."""
    item = {
        "url": "https://example.com/start",
        "finalUrl": "https://evil.example.net/landing",
        "title": "Internal",
        "text": "secret",
        "screenshot": _FAKE_PNG_B64,
    }
    import requests
    orig = _patch_requests(item)
    try:
        res = _with_token(lambda: asyncio.run(_backend().open_page(
            "https://example.com/start", screenshot=True, allow_host=_allow_host(),
        )))
    finally:
        requests.post = orig

    assert res.blocked is True
    assert res.ok is False
    assert res.screenshot_b64 is None, "no screenshot may be returned on an off-list redirect"
    assert not (res.text or ""), "no page text may be returned on an off-list redirect"


def test_screenshot_capture_failure_degrades_gracefully():
    """A null screenshot from the Actor (capture failed) still yields a
    successful page with ``screenshot_b64=None`` rather than failing."""
    item = {
        "url": "https://example.com/p",
        "finalUrl": "https://example.com/p",
        "title": "Hello",
        "text": "body text",
        "screenshot": None,
    }
    import requests
    orig = _patch_requests(item)
    try:
        res = _with_token(lambda: asyncio.run(_backend().open_page(
            "https://example.com/p", screenshot=True, allow_host=_allow_host(),
        )))
    finally:
        requests.post = orig

    assert res.ok is True, "a failed screenshot must not fail the whole open_page"
    assert res.screenshot_b64 is None
    assert res.text == "body text", "page text must still be captured"


def test_no_screenshot_when_not_requested():
    """``screenshot=False`` neither asks for nor returns an image."""
    item = {
        "url": "https://example.com/p",
        "finalUrl": "https://example.com/p",
        "title": "Hello",
        "text": "body text",
        "screenshot": _FAKE_PNG_B64,  # even if the Actor returned one, ignore it
    }
    capture = {}
    import requests
    orig = _patch_requests(item, capture)
    try:
        res = _with_token(lambda: asyncio.run(_backend().open_page(
            "https://example.com/p", screenshot=False, allow_host=_allow_host(),
        )))
    finally:
        requests.post = orig

    assert res.ok is True
    assert res.screenshot_b64 is None, "no screenshot when not requested"
    assert capture["body"]["customData"]["screenshot"] is False, (
        "open_page(screenshot=False) must not ask the Actor to capture one"
    )


def _run_all():
    tests = [
        test_screenshot_requested_and_returned,
        test_screenshot_gated_by_allow_list_on_redirect,
        test_screenshot_capture_failure_degrades_gracefully,
        test_no_screenshot_when_not_requested,
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
