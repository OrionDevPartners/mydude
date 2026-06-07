"""Concrete browser backends.

Each backend implements the vendor-agnostic ``BrowserBackend`` contract. Adding
a backend = add a class here + register it in ``registry.py`` + add a
``[browserbackends.<key>]`` block in config/providers.toml. Heavy imports
(playwright) are done lazily inside methods so the app boots even when no
browser capability is configured.
"""
from __future__ import annotations

import base64
import glob
import os
from pathlib import Path

from src.browser.base import BrowserBackend, BrowserResult
from src.providers.secrets import get_secret, get_env


def _playwright_importable() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _chromium_installed() -> bool:
    """Best-effort check that a Playwright Chromium build is present on disk."""
    candidates = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.append(str(Path.home() / ".cache" / "ms-playwright"))
    for root in candidates:
        if root and glob.glob(os.path.join(root, "chromium-*")):
            return True
    return False


async def _extract_page(page, url, backend_key, timeout_ms, screenshot, max_chars, allow_host=None):
    """Shared navigation/extraction for any Playwright-driven page.

    When ``allow_host`` is provided, a route interceptor aborts every main-frame
    navigation hop (including server/JS redirects) to a host the predicate
    rejects — BEFORE the request leaves the browser. This closes the redirect
    TOCTOU/SSRF gap that a post-navigation check cannot (the hop would already
    have happened).
    """
    from urllib.parse import urlparse

    blocked = {"host": None}

    if allow_host is not None:
        async def _route(route, request):
            try:
                if request.is_navigation_request() and request.frame == page.main_frame:
                    h = (urlparse(request.url).hostname or "").lower()
                    if not allow_host(h):
                        blocked["host"] = h or request.url
                        await route.abort()
                        return
                await route.continue_()
            except Exception:
                # If interception itself errors, fail closed by aborting.
                try:
                    await route.abort()
                except Exception:
                    pass

        await page.route("**/*", _route)

    try:
        resp = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception:
        if blocked["host"]:
            return BrowserResult(
                ok=False,
                backend=backend_key,
                url=url,
                final_url=blocked["host"],
                blocked=True,
                error=(
                    "Navigation blocked: a redirect targeted '%s', which is not "
                    "in the browse allow-list." % blocked["host"]
                ),
                attempts=[backend_key],
            )
        raise
    # A redirect that resolved without raising but still landed off-list (belt
    # and suspenders alongside the interceptor).
    if allow_host is not None:
        final_host = (urlparse(page.url).hostname or "").lower()
        if not allow_host(final_host):
            return BrowserResult(
                ok=False, backend=backend_key, url=url, final_url=page.url, blocked=True,
                error="Navigation blocked: final host '%s' is not in the browse allow-list." % final_host,
                attempts=[backend_key],
            )
    title = await page.title()
    try:
        text = await page.inner_text("body")
    except Exception:
        text = ""
    shot = None
    if screenshot:
        try:
            raw = await page.screenshot(type="png", full_page=False)
            shot = base64.b64encode(raw).decode("ascii")
        except Exception:
            shot = None
    return BrowserResult(
        ok=True,
        backend=backend_key,
        url=url,
        final_url=page.url,
        title=title,
        text=(text or "").strip()[:max_chars],
        screenshot_b64=shot,
        attempts=[backend_key],
    )


_USERNAME_SELECTORS = [
    "input[type=email]",
    "input[autocomplete=username]",
    "input[name*=email i]",
    "input[id*=email i]",
    "input[name*=user i]",
    "input[id*=user i]",
    "input[name*=login i]",
    "input[type=text]",
]
_PASSWORD_SELECTOR = "input[type=password]"
_OTP_SELECTORS = [
    "input[autocomplete=one-time-code]",
    "input[name*=otp i]",
    "input[id*=otp i]",
    "input[name*=code i]",
    "input[id*=code i]",
    "input[name*=verification i]",
]
_NEXT_SELECTORS = [
    "button[type=submit]",
    "input[type=submit]",
    "button:has-text('Next')",
    "button:has-text('Continue')",
]
_SUBMIT_SELECTORS = [
    "button[type=submit]",
    "input[type=submit]",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "button:has-text('Login')",
    "button:has-text('Continue')",
    "button:has-text('Submit')",
]
DEFAULT_CANCEL_TEXTS = [
    "Cancel subscription",
    "Cancel membership",
    "Cancel plan",
    "Continue to cancel",
    "Confirm cancellation",
    "Confirm cancel",
    "Yes, cancel",
    "End membership",
    "Cancel anyway",
    "Finish cancellation",
]


def _host_of(u):
    from urllib.parse import urlparse
    return (urlparse(u or "").hostname or "").lower()


async def _install_allow_host_route(page, allow_host, blocked):
    if allow_host is None:
        return
    from urllib.parse import urlparse

    async def _route(route, request):
        try:
            if request.is_navigation_request() and request.frame == page.main_frame:
                h = (urlparse(request.url).hostname or "").lower()
                if not allow_host(h):
                    blocked["host"] = h or request.url
                    await route.abort()
                    return
            await route.continue_()
        except Exception:
            try:
                await route.abort()
            except Exception:
                pass

    await page.route("**/*", _route)


async def _snapshot(page, backend_key, url, max_chars, ok=True, error=None):
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        text = await page.inner_text("body")
    except Exception:
        text = ""
    shot = None
    try:
        raw = await page.screenshot(type="png", full_page=False)
        shot = base64.b64encode(raw).decode("ascii")
    except Exception:
        shot = None
    return BrowserResult(
        ok=ok,
        backend=backend_key,
        url=url,
        final_url=getattr(page, "url", url),
        title=title,
        text=(text or "").strip()[:max_chars],
        screenshot_b64=shot,
        error=error,
        attempts=[backend_key],
    )


async def _fill_first(page, selectors, value):
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(value)
                return True
        except Exception:
            continue
    return False


async def _click_first(page, selectors):
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _is_visible(page, selector):
    try:
        el = await page.query_selector(selector)
        return bool(el and await el.is_visible())
    except Exception:
        return False


async def _has_captcha(page):
    try:
        content = (await page.content()).lower()
    except Exception:
        return False
    return ("recaptcha" in content or "g-recaptcha" in content
            or "hcaptcha" in content or "captcha-delivery" in content)


async def _wait_for_login_form(page, timeout_ms):
    """Wait for a client-rendered login form to mount before interacting.

    Real sign-in pages are almost always SPAs whose username/password inputs are
    absent at ``domcontentloaded`` and hydrate a beat later. Filling immediately
    races that render — the field isn't there, nothing gets typed, the submit
    advances nowhere, and the flow misreports "no password field / needs you".
    Waiting for the first field to actually appear makes the heuristics reliable.
    """
    selector = ", ".join(_USERNAME_SELECTORS + [_PASSWORD_SELECTOR])
    try:
        await page.wait_for_selector(selector, state="visible", timeout=min(timeout_ms, 15000))
    except Exception:
        pass


async def _do_login(page, login_url, account_url, username, password, otp,
                    backend_key, timeout_ms, max_chars, allow_host):
    """Drive a generic login and land on the account page.

    Returns a BrowserResult only on failure/needs-user/blocked; returns None on
    success, leaving ``page`` authenticated and on the account/billing URL.
    """
    blocked = {"host": None}
    await _install_allow_host_route(page, allow_host, blocked)

    def _blocked():
        return BrowserResult(
            ok=False, backend=backend_key, url=login_url, final_url=blocked["host"],
            blocked=True,
            error="Navigation blocked: a hop targeted '%s', which is not in the "
                  "browse allow-list." % blocked["host"],
            attempts=[backend_key],
        )

    try:
        await page.goto(login_url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception:
        if blocked["host"]:
            return _blocked()
        raise

    # Sign-in pages are SPAs: wait for the form to mount before typing, or the
    # fill races the render and silently no-ops.
    await _wait_for_login_form(page, timeout_ms)

    await _fill_first(page, _USERNAME_SELECTORS, username or "")
    if not await _is_visible(page, _PASSWORD_SELECTOR):
        # Two-step login: advance past the identifier page first, then wait for
        # the password field to actually render rather than guessing a delay.
        await _click_first(page, _NEXT_SELECTORS)
        try:
            await page.wait_for_selector(_PASSWORD_SELECTOR, state="visible",
                                         timeout=min(timeout_ms, 12000))
        except Exception:
            await page.wait_for_timeout(1800)
        if blocked["host"]:
            return _blocked()

    if not await _fill_first(page, [_PASSWORD_SELECTOR], password or ""):
        return await _snapshot(
            page, backend_key, page.url, max_chars, ok=False,
            error="Could not find a password field — the site may use SSO, a "
                  "passkey, or otherwise blocked automated login. Finish this "
                  "sign-in yourself.",
        )

    if not await _click_first(page, _SUBMIT_SELECTORS):
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
    await page.wait_for_timeout(2500)
    if blocked["host"]:
        return _blocked()

    if await _has_captcha(page):
        return await _snapshot(
            page, backend_key, page.url, max_chars, ok=False,
            error="A CAPTCHA was presented — MyDude can't solve it. Please "
                  "complete this login yourself.",
        )

    if any([await _is_visible(page, s) for s in _OTP_SELECTORS]):
        if not otp:
            return await _snapshot(
                page, backend_key, page.url, max_chars, ok=False,
                error="The site asked for a one-time code and none was available. "
                      "SMS codes can be read from your Mac via the SSH bridge; "
                      "authenticator-app codes can't be read.",
            )
        await _fill_first(page, _OTP_SELECTORS, otp)
        if not await _click_first(page, _SUBMIT_SELECTORS):
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
        await page.wait_for_timeout(2500)
        if blocked["host"]:
            return _blocked()

    target = account_url or page.url
    try:
        await page.goto(target, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception:
        if blocked["host"]:
            return _blocked()
    if allow_host is not None and not allow_host(_host_of(page.url)):
        blocked["host"] = _host_of(page.url)
        return _blocked()
    return None


async def _do_cancel(page, confirm_texts, backend_key, timeout_ms, max_chars):
    """Click through cancel/confirm controls on the current (logged-in) page.

    This is the irreversible step; it must only ever be called after an explicit
    user confirmation upstream. Returns a BrowserResult.
    """
    texts = confirm_texts or DEFAULT_CANCEL_TEXTS
    clicked = []
    for _ in range(4):  # cancel flows are typically 1-3 confirmation steps
        progressed = False
        for label in texts:
            try:
                loc = page.get_by_role("button", name=label, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    clicked.append(label)
                    progressed = True
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                continue
        if not progressed:
            # Fall back to any link/button containing the text.
            for label in texts:
                try:
                    loc = page.locator("a, button").filter(has_text=label)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        clicked.append(label)
                        progressed = True
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    continue
        if not progressed:
            break
    if not clicked:
        return await _snapshot(
            page, backend_key, page.url, max_chars, ok=False,
            error="Couldn't find a cancel control automatically. The account page "
                  "is shown so you can finish the cancellation yourself.",
        )
    snap = await _snapshot(page, backend_key, page.url, max_chars, ok=True)
    snap.text = ("Clicked: %s\n\n%s" % (" → ".join(clicked), snap.text or ""))[:max_chars]
    return snap


class LocalPlaywrightBackend(BrowserBackend):
    """Headless Chromium running inside this container. Free but heavy; may be
    unavailable in deployment if the Chromium build was not provisioned."""

    def _available(self) -> bool:
        return _playwright_importable() and _chromium_installed()

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        from playwright.async_api import async_playwright

        browser = None
        pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()
            return await _extract_page(
                page, url, self.key, timeout_ms, screenshot, max_chars, allow_host
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass

    async def login_page(self, login_url, account_url, username, password, *,
                         otp=None, timeout_ms=45000, max_chars=4000, allow_host=None):
        from playwright.async_api import async_playwright

        browser = None
        pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _snapshot(page, self.key, account_url or page.url, max_chars)
        finally:
            await _teardown(browser, pw)

    async def cancel_action(self, login_url, account_url, username, password, *,
                            otp=None, confirm_texts=None, timeout_ms=45000,
                            max_chars=4000, allow_host=None):
        from playwright.async_api import async_playwright

        browser = None
        pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _do_cancel(page, confirm_texts, self.key, timeout_ms, max_chars)
        finally:
            await _teardown(browser, pw)


async def _teardown(browser, pw):
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            pass


class BrowserbaseBackend(BrowserBackend):
    """Cloud Chromium via Browserbase. Reliable in deployment. Creates a
    session over the REST API then drives it with Playwright over CDP."""

    API_BASE = "https://api.browserbase.com/v1"

    def _available(self) -> bool:
        # secrets_present() (checked by base.available) covers the API key +
        # project id; we also need playwright to drive the remote session.
        return _playwright_importable()

    def _create_session(self):
        import requests

        api_key = get_secret("BROWSERBASE_API_KEY")
        project_id = get_secret("BROWSERBASE_PROJECT_ID")
        r = requests.post(
            self.API_BASE + "/sessions",
            headers={"X-BB-API-Key": api_key, "Content-Type": "application/json"},
            json={"projectId": project_id},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        connect_url = data.get("connectUrl")
        if not connect_url:
            raise RuntimeError("Browserbase did not return a connectUrl")
        return connect_url

    async def _connect(self):
        """Open a remote Browserbase session and return (pw, browser, page)."""
        import asyncio

        from playwright.async_api import async_playwright

        connect_url = await asyncio.to_thread(self._create_session)
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(connect_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, browser, page

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        pw = None
        browser = None
        try:
            pw, browser, page = await self._connect()
            return await _extract_page(
                page, url, self.key, timeout_ms, screenshot, max_chars, allow_host
            )
        finally:
            await _teardown(browser, pw)

    async def login_page(self, login_url, account_url, username, password, *,
                         otp=None, timeout_ms=45000, max_chars=4000, allow_host=None):
        pw = None
        browser = None
        try:
            pw, browser, page = await self._connect()
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _snapshot(page, self.key, account_url or page.url, max_chars)
        finally:
            await _teardown(browser, pw)

    async def cancel_action(self, login_url, account_url, username, password, *,
                            otp=None, confirm_texts=None, timeout_ms=45000,
                            max_chars=4000, allow_host=None):
        pw = None
        browser = None
        try:
            pw, browser, page = await self._connect()
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _do_cancel(page, confirm_texts, self.key, timeout_ms, max_chars)
        finally:
            await _teardown(browser, pw)


class _ConfigReadyStub(BrowserBackend):
    """A backend that is wired into config + selection but not yet implemented.

    It reports unavailable (so the engine fails over past it) and returns a
    clear, honest message rather than pretending to browse.
    """

    vendor = "this backend"

    def _available(self) -> bool:
        return False

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        return BrowserResult(
            ok=False,
            backend=self.key,
            url=url,
            error=(
                "Backend '%s' (%s) is config-ready but not yet implemented. "
                "Add its credentials and an implementation to enable it."
                % (self.key, self.vendor)
            ),
            attempts=[self.key],
        )


class ApifyBackend(_ConfigReadyStub):
    vendor = "Apify"


class AgentCoreBackend(_ConfigReadyStub):
    vendor = "AWS Bedrock AgentCore Browser"


class AzureBackend(_ConfigReadyStub):
    vendor = "Azure Playwright Testing"
