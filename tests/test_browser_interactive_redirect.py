"""Tests that the *interactive* login/cancel redirect protection actually holds.

``tests/test_browser_redirect_failover.py`` proves the simple "open a page"
path (``_extract_page``) aborts an off-list redirect before any content is read.
But the interactive sign-in / cancel flow has its OWN, separately-coded redirect
protection in ``_do_login`` / ``_do_cancel`` (``src/browser/backends.py``):

  * a per-hop ``allow_host`` route interceptor installed via
    ``_install_allow_host_route`` (aborts a navigation to an off-list host
    BEFORE the request leaves the browser), plus
  * post-step ``blocked["host"]`` checks after the identifier page, after the
    sign-in submit, and a final post-navigation host check after landing on the
    account/billing URL.

The risk this guards against is acute: a sign-in page that bounces to an
off-list / internal host mid-flow could be handed the user's password or OTP, or
the cancel flow could click a "cancel" control on an attacker-controlled page.
This suite proves that never happens — when a hop goes off-list:

  1. ``_do_login`` returns ``blocked=True`` and NO password/OTP is ever typed
     while the page is on an off-list host (it is blocked first), for both a
     redirect AFTER the identifier step and a redirect navigating to the
     account URL.
  2. ``cancel_action`` is blocked in its login phase, so NO cancel control is
     ever scanned or clicked on the off-list host.
  3. A blocked interactive flow does NOT fail over to a second backend (a policy
     block is not a backend fault — retrying would re-attempt the forbidden
     navigation). This drives the REAL ``_do_login`` / ``_do_cancel`` through
     ``BrowserEngine`` over two fake CDP backends.

The Playwright page and the remote CDP session are faked, so the suite runs
offline with no credentials and no real browser (local Chromium can't launch in
this container — see tests/test_browser_redirect_failover.py).

Runnable two ways:
  * ``python tests/test_browser_interactive_redirect.py``  (standalone, non-zero exit on failure)
  * ``pytest tests/test_browser_interactive_redirect.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.browser.backends import _CDPConnectBackend, _do_login
from src.browser.base import BrowserBackendSpec, BrowserResult
from src.browser.engine import BrowserEngine
from src.swarm.policy import PolicyEngine

_ALLOWED = "example.com"
_PASSWORD_SELECTOR = "input[type=password]"


def _allow_host():
    """Real policy predicate with a known one-domain allow-list."""
    os.environ["BROWSER_ALLOWED_DOMAINS"] = _ALLOWED
    return PolicyEngine().is_host_allowed


# --------------------------------------------------------------------------- #
# Fakes mimicking the slice of the Playwright page API the login/cancel flow
# touches. Navigation hops are fed through the registered route handler exactly
# as a real 30x / JS redirect would be, so the allow_host interceptor runs for
# real against each hop.
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


class _FakeKeyboard:
    async def press(self, *a, **k):
        return None


class _EmptyLocator:
    """A locator that matches nothing — used so the cancel walker, if it ever
    ran, would find no control (it must not run at all on a blocked login)."""

    def filter(self, *a, **k):
        return self

    def nth(self, i):
        return self

    async def count(self):
        return 0

    async def is_visible(self):
        return False


class _FakeElement:
    def __init__(self, page, kind, visible=True, on_click=None):
        self.page = page
        self.kind = kind
        self._visible = visible
        self._on_click = on_click

    async def is_visible(self):
        return self._visible

    async def is_enabled(self):
        return True

    async def fill(self, value):
        # Record WHAT was typed and the host the page is on at that moment, so a
        # test can prove no credential is ever typed on an off-list host.
        self.page.fills.append((self.kind, self.page.url))

    async def click(self):
        if self._on_click is not None:
            await self._on_click()

    async def inner_text(self):
        return ""

    async def get_attribute(self, name):
        return None


class _LoginFakePage:
    """A faithful slice of the Playwright page driven by ``_do_login``.

    Parameters model the login shape under test:
      * ``two_step``      — password is hidden until a "Next" control is clicked
                            (an identifier page), exercising the post-identifier
                            block. When False the password field is present
                            immediately (single-step form).
      * ``click_redirect``— URL a Next/Submit click navigates to. Set to an
                            off-list host to redirect AFTER the identifier step.
                            None means the click triggers no navigation.

    ``goto`` and every click run the candidate URL through the registered route
    handler as a navigation request. An off-list hop is aborted by the
    interceptor (``route.abort()``); ``goto`` then raises like Playwright's
    ``net::ERR_ABORTED`` and ``self.url`` is left unchanged (the forbidden host
    is never actually loaded).
    """

    def __init__(self, two_step=False, click_redirect=None):
        self.main_frame = _FakeFrame()
        self.url = None
        self.keyboard = _FakeKeyboard()
        self._handler = None
        self._two_step = two_step
        self._password_visible = not two_step
        self._click_redirect = click_redirect
        # Observability for assertions.
        self.fills = []          # [(kind, url_at_fill)]
        self.clicks = 0          # count of control clicks
        self.content_calls = 0   # title/inner_text/screenshot reads
        self.cancel_scans = 0    # any cancel-walker scan (must stay 0 on block)

        self._username_el = _FakeElement(self, "username")
        self._password_el = _FakeElement(self, "password")
        self._button_el = _FakeElement(self, "button", on_click=self._on_button_click)

    # -- routing / navigation --------------------------------------------- #
    async def route(self, pattern, handler):
        self._handler = handler

    async def _navigate(self, url, raise_on_abort):
        if self._handler is not None:
            route = _FakeRoute()
            req = _FakeRequest(url, self.main_frame)
            await self._handler(route, req)
            if route.aborted:
                if raise_on_abort:
                    raise RuntimeError("net::ERR_ABORTED at %s" % url)
                return False
        self.url = url
        # A redirect that actually loaded an off-list host would reveal its
        # password field; the interceptor must abort first so this never runs.
        return True

    async def goto(self, url, timeout=None, wait_until=None):
        await self._navigate(url, raise_on_abort=True)
        return object()

    async def _on_button_click(self):
        self.clicks += 1
        if self._click_redirect is not None:
            # If the redirect is permitted it would reveal the password field;
            # if off-list it is aborted (raises, caught by _click_first).
            ok = await self._navigate(self._click_redirect, raise_on_abort=True)
            if ok:
                self._password_visible = True

    # -- element lookup --------------------------------------------------- #
    async def query_selector(self, selector):
        s = selector.lower()
        if "password" in s:
            return self._password_el if self._password_visible else None
        if any(m in s for m in ("otp", "one-time", "code", "verification")):
            return None  # no OTP field present
        if "submit" in s or "button" in s:
            return self._button_el
        if s.startswith("input"):
            return self._username_el
        return None

    # -- waits / misc ----------------------------------------------------- #
    async def wait_for_selector(self, selector, state=None, timeout=None):
        if selector == _PASSWORD_SELECTOR and not self._password_visible:
            raise RuntimeError("timeout waiting for password field")
        return object()

    async def wait_for_timeout(self, ms):
        return None

    async def content(self):
        return "<html><body>Sign in</body></html>"  # no captcha markers

    async def title(self):
        self.content_calls += 1
        return "Account"

    async def inner_text(self, selector):
        self.content_calls += 1
        return "account page body"

    async def screenshot(self, **kwargs):
        self.content_calls += 1
        return b"\x89PNG-fake"

    # -- cancel-walker surface (must never be touched on a blocked login) -- #
    async def wait_for_load_state(self, *a, **k):
        self.cancel_scans += 1

    def get_by_role(self, *a, **k):
        self.cancel_scans += 1
        return _EmptyLocator()

    def locator(self, *a, **k):
        self.cancel_scans += 1
        return _EmptyLocator()


def _assert_no_offlist_fill(page, allow_host):
    """No credential may be typed while the page sits on an off-list host."""
    from urllib.parse import urlparse
    for kind, url_at_fill in page.fills:
        host = (urlparse(url_at_fill or "").hostname or "").lower()
        assert allow_host(host), (
            "%s was filled while the page was on off-list host %r" % (kind, host)
        )


# --------------------------------------------------------------------------- #
# 1. _do_login direct: redirect AFTER the identifier step is blocked
# --------------------------------------------------------------------------- #
def test_login_redirect_after_identifier_is_blocked():
    """Two-step login whose 'Next' bounces to an internal host: blocked, no pw."""
    allow_host = _allow_host()
    page = _LoginFakePage(two_step=True, click_redirect="http://169.254.169.254/login")

    res = asyncio.run(_do_login(
        page, "https://example.com/login", "https://example.com/account",
        "user@example.com", "s3cret", None, "fake", 5000, 4000, allow_host,
    ))

    assert isinstance(res, BrowserResult)
    assert res.blocked is True, "an off-list hop after the identifier step must block"
    assert res.ok is False
    # The password must never have been typed at all (we block before reaching it).
    assert not any(k == "password" for k, _ in page.fills), (
        "the password was filled despite the off-list redirect: %r" % page.fills
    )
    _assert_no_offlist_fill(page, allow_host)
    assert "169.254.169.254" in (res.error or "") or (res.final_url or "").startswith("169.254")
    assert page.content_calls == 0, "no content may be read from the forbidden host"


# --------------------------------------------------------------------------- #
# 2. _do_login direct: redirect navigating to the account URL is blocked
# --------------------------------------------------------------------------- #
def test_login_redirect_on_account_nav_is_blocked():
    """Single-step login succeeds, then the account URL is off-list: blocked.

    The password legitimately gets typed on the allowed sign-in host, but the
    subsequent navigation to an off-list account URL is aborted, so no content
    is captured and no credential is ever typed off-list.
    """
    allow_host = _allow_host()
    page = _LoginFakePage(two_step=False, click_redirect=None)

    res = asyncio.run(_do_login(
        page, "https://example.com/login", "http://127.0.0.1/account",
        "user@example.com", "s3cret", None, "fake", 5000, 4000, allow_host,
    ))

    assert isinstance(res, BrowserResult)
    assert res.blocked is True, "an off-list account navigation must block"
    assert res.ok is False
    # Password may be typed on the allowed sign-in host, but never off-list.
    _assert_no_offlist_fill(page, allow_host)
    assert page.url == "https://example.com/login", (
        "the page must never have actually loaded the off-list account host"
    )
    assert page.content_calls == 0, "no content may be read from the forbidden host"


# --------------------------------------------------------------------------- #
# 3. Control: a fully in-allow-list login completes (no false positives)
# --------------------------------------------------------------------------- #
def test_allowed_login_completes_without_block():
    """A login + account navigation entirely within the allow-list is NOT blocked."""
    allow_host = _allow_host()
    page = _LoginFakePage(two_step=False, click_redirect=None)

    res = asyncio.run(_do_login(
        page, "https://example.com/login", "https://www.example.com/account",
        "user@example.com", "s3cret", None, "fake", 5000, 4000, allow_host,
    ))

    assert res is None, "an allowed login must succeed (returns None), got: %r" % res
    assert any(k == "password" for k, _ in page.fills), "password should have been typed"
    assert page.url == "https://www.example.com/account"


# --------------------------------------------------------------------------- #
# Fake interactive CDP backend (drives the REAL _do_login / _do_cancel)
# --------------------------------------------------------------------------- #
class _FakeClosable:
    async def close(self):
        return None

    async def stop(self):
        return None


class _FakeInteractiveBackend(_CDPConnectBackend):
    """A real ``_CDPConnectBackend`` (so login_page/cancel_action run the real
    ``_do_login`` / ``_do_cancel``) whose remote session is a fake page."""

    def __init__(self, key, page):
        super().__init__(BrowserBackendSpec(key=key, adapter="fake"))
        self._page = page
        self.connect_calls = 0

    def _available(self):
        return True

    async def open_page(self, url, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def _connect(self):
        self.connect_calls += 1
        return _FakeClosable(), _FakeClosable(), self._page, None


# --------------------------------------------------------------------------- #
# 4. Engine: a blocked interactive LOGIN does not fail over to a 2nd backend
# --------------------------------------------------------------------------- #
def test_interactive_login_block_does_not_failover():
    allow_host = _allow_host()
    page1 = _LoginFakePage(two_step=True, click_redirect="http://127.0.0.1/login")
    page2 = _LoginFakePage(two_step=False, click_redirect=None)  # would "succeed"
    b1 = _FakeInteractiveBackend("primary", page1)
    b2 = _FakeInteractiveBackend("secondary", page2)

    engine = BrowserEngine()
    engine.backends = lambda: [b1, b2]  # type: ignore[assignment]

    res = asyncio.run(engine.login_page(
        "https://example.com/login", "https://example.com/account",
        "user@example.com", "s3cret", allow_host=allow_host,
    ))

    assert res.blocked is True, "the block must be surfaced, not swallowed"
    assert b1.connect_calls == 1, "primary backend should be tried once"
    assert b2.connect_calls == 0, (
        "a policy block must NOT fail over to another backend (it would re-attempt "
        "the forbidden navigation), but the secondary connected %d time(s)"
        % b2.connect_calls
    )
    assert res.attempts == ["primary"]
    assert not any(k == "password" for k, _ in page1.fills)
    _assert_no_offlist_fill(page1, allow_host)


# --------------------------------------------------------------------------- #
# 5. Engine: a blocked CANCEL is stopped before any cancel control is clicked
# --------------------------------------------------------------------------- #
def test_interactive_cancel_block_before_any_cancel_control():
    """cancel_action's login phase goes off-list: the cancel walker never runs.

    The block happens inside ``_do_login`` (the login phase of the cancel flow),
    so ``_do_cancel`` is never reached — no cancel/confirm control is scanned or
    clicked on the off-list host, and there is no fail-over to a 2nd backend.
    """
    allow_host = _allow_host()
    page1 = _LoginFakePage(two_step=True, click_redirect="http://169.254.169.254/login")
    page2 = _LoginFakePage(two_step=False, click_redirect=None)
    b1 = _FakeInteractiveBackend("primary", page1)
    b2 = _FakeInteractiveBackend("secondary", page2)

    engine = BrowserEngine()
    engine.backends = lambda: [b1, b2]  # type: ignore[assignment]

    res = asyncio.run(engine.cancel_action(
        "https://example.com/login", "https://example.com/account",
        "user@example.com", "s3cret", allow_host=allow_host,
    ))

    assert res.blocked is True
    assert page1.cancel_scans == 0, (
        "the cancel walker must NOT run after a blocked login (it scanned %d time(s))"
        % page1.cancel_scans
    )
    assert b1.connect_calls == 1
    assert b2.connect_calls == 0, (
        "a blocked cancel flow must NOT fail over, but the secondary connected "
        "%d time(s)" % b2.connect_calls
    )
    assert res.attempts == ["primary"]
    assert not any(k == "password" for k, _ in page1.fills)
    _assert_no_offlist_fill(page1, allow_host)


def _run_all():
    tests = [
        test_login_redirect_after_identifier_is_blocked,
        test_login_redirect_on_account_nav_is_blocked,
        test_allowed_login_completes_without_block,
        test_interactive_login_block_does_not_failover,
        test_interactive_cancel_block_before_any_cancel_control,
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
