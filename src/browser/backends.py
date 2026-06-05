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

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        import asyncio

        from playwright.async_api import async_playwright

        connect_url = await asyncio.to_thread(self._create_session)
        pw = None
        browser = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(connect_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
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
