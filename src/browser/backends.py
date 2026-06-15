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
import re
from pathlib import Path

from src.browser.base import BrowserBackend, BrowserResult
from src.providers.secrets import get_secret, get_env, require_secret


def _playwright_importable() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _boto3_importable() -> bool:
    """True if the AWS SDK (boto3 + botocore) is importable — required to start
    and SigV4-sign an AgentCore browser session."""
    try:
        import boto3  # noqa: F401
        import botocore  # noqa: F401
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
# Real-world cancel/confirm control labels, ordered roughly by the step they
# appear in (initiate → progress → confirm). Matching is case-insensitive
# substring (``exact=False``), so each entry also catches close variants. The
# list is intentionally broad because account pages differ per provider, and the
# two-phase confirmation gate upstream bounds the blast radius of a wrong click.
# Labels seen on the cataloged providers:
#   Netflix      "Cancel Membership" → "Finish Cancellation"
#   Spotify      "Cancel Premium" / "Cancel Plan" → "Yes, cancel" / "Continue to cancel"
#   Amazon Prime "End Membership" / "Cancel My Benefits" → "End My Benefits" / "End Now"
#   Disney+      "Cancel Subscription" → "Complete Cancellation"
#   Hulu         "Cancel Your Subscription" → "Continue to Cancel"
#   YouTube Prem "Continue to cancel" → "Deactivate" / "Yes, deactivate"
#   Apple        "Cancel Subscription" → "Confirm"
DEFAULT_CANCEL_TEXTS = [
    # Initiating controls
    "Cancel subscription",
    "Cancel membership",
    "Cancel Premium",
    "Cancel plan",
    "Cancel your subscription",
    "Cancel my plan",
    "Cancel my subscription",
    "Cancel my membership",
    "End membership",
    "End my benefits",
    "Cancel my benefits",
    "Deactivate membership",
    "Cancel auto-renewal",
    "Turn off auto-renew",
    # Progression controls
    "Continue to cancel",
    "Continue cancellation",
    "Proceed to cancel",
    "I still want to cancel",
    "Cancel anyway",
    # Retention-screen decline controls. Real cancel flows interpose "pause
    # instead" / discount-offer / survey / "are you sure?" interstitials whose
    # prominent button keeps you subscribed. These are the *decline* controls
    # that keep progressing toward the real cancel — try them before giving up.
    "No thanks, continue to cancel",
    "No thanks, cancel",
    "No thanks",
    "No, thanks",
    "No, continue to cancel",
    "Decline offer",
    "Decline and cancel",
    "Decline and continue",
    "Skip and continue",
    "Skip offer",
    "Not now, cancel",
    "Continue canceling",
    "Continue cancelling",
    "Continue with cancellation",
    # Confirmation controls
    "Confirm cancellation",
    "Complete cancellation",
    "Finish cancellation",
    "Confirm cancel",
    "Yes, cancel",
    "Yes, deactivate",
    "End now",
    "End my membership",
]


# Controls that KEEP you subscribed. These must never be clicked while walking a
# cancel flow, even when a substring of a cancel label happens to appear inside
# their text (e.g. a "Cancel anyway? No — keep my plan" retention button). They
# are matched as a safety guard against accidentally accepting a retention offer.
RETENTION_KEEP_TEXTS = [
    "keep my plan",
    "keep my subscription",
    "keep my membership",
    "keep my benefits",
    "keep plan",
    "keep subscription",
    "keep membership",
    "keep benefits",
    "keep my account",
    "keep watching",
    "keep listening",
    "stay subscribed",
    "stay a member",
    "stay premium",
    "remain subscribed",
    "remain a member",
    "no, keep",
    "don't cancel",
    "do not cancel",
    "never mind",
    "nevermind",
    "go back",
    "remind me later",
    "maybe later",
    "ask me later",
    "pause instead",
    "pause membership",
    "pause my membership",
    "pause subscription",
    "pause my subscription",
    "pause plan",
    "pause my plan",
    "pause billing",
    "get the discount",
    "claim offer",
    "claim discount",
    "accept offer",
    "accept discount",
    "apply discount",
    "redeem offer",
]


def _looks_like_keep(text):
    """True if a control's text reads as a retention/keep/pause/offer control.

    Used to make sure the cancel walker declines retention offers instead of
    accidentally clicking a "keep my plan" / "pause instead" / "accept discount"
    button on an "are you sure?" interstitial.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(k in t for k in RETENTION_KEEP_TEXTS)


# Controls that actually CONFIRM the cancellation (the terminal step of a flow).
# Used to decide whether a walk truly finished cancelling vs. merely clicked an
# initiate/progress control before stalling on a retention wall. Kept as a
# subset of the confirmation labels in DEFAULT_CANCEL_TEXTS.
CONFIRM_CANCEL_TEXTS = [
    "confirm cancellation",
    "complete cancellation",
    "finish cancellation",
    "confirm cancel",
    "yes, cancel",
    "yes, deactivate",
    "deactivate membership",
    "end now",
    "end my membership",
    "end my benefits",
]


def _looks_like_confirm(text):
    """True if a control's text reads as a terminal cancel-confirmation control.

    Lets the walker tell "we actually confirmed the cancellation" apart from
    "we only clicked an initiate/progress control and then stalled".
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(c in t for c in CONFIRM_CANCEL_TEXTS)


async def _safe_text(el):
    """Best-effort visible text of a Playwright element handle / locator."""
    for attr in ("inner_text", "text_content"):
        fn = getattr(el, attr, None)
        if fn is None:
            continue
        try:
            t = await fn()
            if t:
                return t
        except Exception:
            continue
    return ""


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


async def _is_enabled(el):
    """Best-effort enabled-state of a Playwright element handle / locator.

    Defaults to True when the element exposes no ``is_enabled`` (or it errors)
    so callers degrade to the prior visibility-only behaviour rather than
    treating every control as disabled.
    """
    fn = getattr(el, "is_enabled", None)
    if fn is None:
        return True
    try:
        return bool(await fn())
    except Exception:
        return True


async def _input_label_text(el):
    """Best-effort human-readable label for a form input (radio / option).

    Tries ``aria-label`` and ``value`` attributes first, then falls back to the
    text of the input's enclosing / associated ``<label>``. Used only to make
    sure a survey answer we auto-select isn't really a "keep my plan" control.
    """
    for attr in ("aria-label", "value"):
        try:
            v = await el.get_attribute(attr)
            if v and v.strip():
                return v
        except Exception:
            pass
    try:
        txt = await el.evaluate(
            "e => { const l = e.closest('label') || (e.id && "
            "document.querySelector('label[for=\"' + e.id + '\"]')); "
            "return l ? l.innerText : ''; }"
        )
        if txt and txt.strip():
            return txt
    except Exception:
        pass
    return ""


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
                      "SMS codes can be read from your Mac via the SSH bridge and "
                      "emailed codes from a connected Gmail; authenticator-app "
                      "codes can't be read.",
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


async def _click_progressing_control(page, texts, gated=None):
    """Click the next cancel-progressing control on the current page.

    Walks the candidate labels (real ``<button>`` first, then link / role=button
    styled controls) and clicks the first visible, ENABLED match that is NOT a
    retention "keep / pause / stay subscribed / accept offer" control. Returns
    the clicked control's text, or ``None`` when nothing progresses the cancel.

    The keep-guard (``_looks_like_keep``) means that even if a cancel label is a
    substring of a retention button's text (e.g. searching "Cancel anyway" finds
    "Cancel anyway? No — keep my plan"), that button is skipped and the genuine
    decline/cancel control next to it is used instead.

    When ``gated`` is a mutable dict, a visible-but-disabled cancel-progressing
    control sets ``gated["found"] = True``. That signals the caller that a real
    cancel control exists but is disabled (typically until a required survey is
    answered), so it can satisfy the survey and retry rather than giving up.
    """
    finders = (
        lambda label: page.get_by_role("button", name=label, exact=False),
        lambda label: page.locator("a, button, [role=button]").filter(has_text=label),
    )
    for finder in finders:
        for label in texts:
            try:
                loc = finder(label)
                n = await loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if not await el.is_visible():
                        continue
                    text = await _safe_text(el)
                    if _looks_like_keep(text):
                        continue  # never accept a retention/keep offer
                    if not await _is_enabled(el):
                        # A genuine cancel control that's disabled until a
                        # required survey is filled — flag it for the caller.
                        if gated is not None:
                            gated["found"] = True
                        continue
                    await el.click()
                    return (text or label).strip()
                except Exception:
                    continue
    return None


async def _has_visible_keep_control(page):
    """True if the current page shows a visible retention/keep/pause/offer control.

    Unlike :func:`_click_progressing_control` (which scans by *cancel* labels),
    this scans by the retention/keep labels directly, so it detects a screen that
    offers ONLY "keep / pause / stay subscribed / accept offer" controls — i.e. a
    retention wall with no decline path. That distinguishes "stalled behind a
    retention screen" from "no cancel control here at all".
    """
    finders = (
        lambda label: page.get_by_role("button", name=label, exact=False),
        lambda label: page.locator("a, button, [role=button]").filter(has_text=label),
    )
    for finder in finders:
        for label in RETENTION_KEEP_TEXTS:
            try:
                loc = finder(label)
                n = await loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if await el.is_visible():
                        return True
                except Exception:
                    continue
    return False


async def _satisfy_required_survey(page):
    """Answer a simple required survey gating a cancel-progressing control.

    Some cancel flows ("Tell us why you're leaving") disable the next button
    until a required radio group or dropdown is filled. This selects a single
    neutral reason so the gated control becomes clickable. It NEVER picks an
    option that keeps/pauses the subscription (``_looks_like_keep`` guard), and
    it only ever selects a reason — it does not submit anything itself; the
    keep-guarded ``_click_progressing_control`` still drives the actual cancel.

    Returns True if a choice was made, else False.
    """
    # Radio groups: select the first visible, enabled, non-keep option.
    try:
        radios = page.locator("input[type=radio]")
        n = await radios.count()
    except Exception:
        n = 0
    for i in range(n):
        try:
            r = radios.nth(i)
            if not await r.is_visible() or not await _is_enabled(r):
                continue
            if _looks_like_keep(await _input_label_text(r)):
                continue
            await r.check()
            return True
        except Exception:
            continue
    # Dropdowns: pick the first real (non-placeholder), non-keep option.
    try:
        selects = page.locator("select")
        sn = await selects.count()
    except Exception:
        sn = 0
    for i in range(sn):
        try:
            sel = selects.nth(i)
            if not await sel.is_visible() or not await _is_enabled(sel):
                continue
            options = sel.locator("option")
            m = await options.count()
            for j in range(m):
                opt = options.nth(j)
                val = await opt.get_attribute("value")
                if not val:
                    continue  # placeholder / empty option
                if _looks_like_keep(await _safe_text(opt)):
                    continue
                await sel.select_option(value=val)
                return True
        except Exception:
            continue
    return False


async def _do_cancel(page, confirm_texts, backend_key, timeout_ms, max_chars):
    """Click through cancel/confirm controls on the current (logged-in) page.

    This is the irreversible step; it must only ever be called after an explicit
    user confirmation upstream. Returns a BrowserResult.
    """
    texts = confirm_texts or DEFAULT_CANCEL_TEXTS
    # Account/billing pages are SPAs: the cancel control is rendered client-side
    # and is usually absent right after navigation. Let the network settle and
    # give the first cancel control a moment to mount before scanning, or the
    # heuristic races the render and misreports "couldn't find a cancel control".
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
    except Exception:
        pass
    try:
        first_scan = ", ".join(
            "text=/%s/i" % re.escape(t) for t in texts[:6]
        )
        await page.wait_for_selector(first_scan, state="visible",
                                     timeout=min(timeout_ms, 6000))
    except Exception:
        await page.wait_for_timeout(1500)
    clicked = []
    confirmed = False
    # Allow a few extra steps over the 1-3 "happy path": retention flows can
    # interpose a "pause instead" / discount / survey / "are you sure?" screen
    # between initiate and confirm, adding one or two decline steps.
    for _ in range(6):
        gated = {"found": False}
        text = await _click_progressing_control(page, texts, gated)
        if not text and gated["found"]:
            # A real cancel control exists but is disabled until a required
            # survey ("why are you leaving?") is answered. Pick a neutral
            # reason (never a keep/pause option) and retry the same step.
            if await _satisfy_required_survey(page):
                await page.wait_for_timeout(500)
                text = await _click_progressing_control(page, texts)
        if not text:
            break
        clicked.append(text)
        if _looks_like_confirm(text):
            confirmed = True
        await page.wait_for_timeout(2000)
    if not clicked:
        return await _snapshot(
            page, backend_key, page.url, max_chars, ok=False,
            error="Couldn't find a cancel control automatically. The account page "
                  "is shown so you can finish the cancellation yourself.",
        )
    # Honesty gate: we clicked an initiate/progress control but never reached a
    # terminal confirmation, and the screen we stalled on offers only retention
    # "keep / pause / stay subscribed / accept offer" controls (no decline path).
    # The subscription is almost certainly still active behind the retention wall,
    # so report a needs-you outcome instead of an unqualified success — clicking
    # *something* earlier must not read as "cancelled".
    if not confirmed and await _has_visible_keep_control(page):
        snap = await _snapshot(
            page, backend_key, page.url, max_chars, ok=False,
            error="Stopped on a retention screen — only 'keep / pause / stay "
                  "subscribed' options were found, so the cancellation isn't "
                  "confirmed. The account page is shown so you can finish the "
                  "cancellation yourself.",
        )
        snap.text = ("Clicked: %s\n\n%s" % (" → ".join(clicked), snap.text or ""))[:max_chars]
        return snap
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


class _CDPConnectBackend(BrowserBackend):
    """Base for backends that expose a remote Chromium over a CDP/WebSocket
    endpoint driven by Playwright.

    Subclasses implement:
      * ``_available()`` — deps/secrets readiness (must not raise).
      * ``_connect()`` — open a remote session and return
        ``(pw, browser, page, cleanup)`` where ``cleanup`` is an optional
        zero-arg async callable run after the browser is closed (e.g. to stop a
        billed remote session), or ``None``.

    Because every CDP-driven backend reuses the same ``_extract_page`` /
    ``_do_login`` / ``_do_cancel`` helpers, they all inherit the identical
    governance the local backend has: the redirect allow-list route interceptor
    (SSRF/redirect safety), the "needs you" handling (CAPTCHA, OTP, no password
    field), and the retention-offer-aware cancel walker.
    """

    async def _connect(self):
        raise NotImplementedError

    async def _run(self, body):
        """Open a session, run ``body(page)``, and always tear down the session."""
        pw = None
        browser = None
        cleanup = None
        try:
            pw, browser, page, cleanup = await self._connect()
            return await body(page)
        finally:
            await _teardown(browser, pw)
            if cleanup is not None:
                try:
                    await cleanup()
                except Exception:
                    pass

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        async def body(page):
            return await _extract_page(
                page, url, self.key, timeout_ms, screenshot, max_chars, allow_host
            )
        return await self._run(body)

    async def login_page(self, login_url, account_url, username, password, *,
                         otp=None, timeout_ms=45000, max_chars=4000, allow_host=None):
        async def body(page):
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _snapshot(page, self.key, account_url or page.url, max_chars)
        return await self._run(body)

    async def cancel_action(self, login_url, account_url, username, password, *,
                            otp=None, confirm_texts=None, timeout_ms=45000,
                            max_chars=4000, allow_host=None):
        async def body(page):
            err = await _do_login(page, login_url, account_url, username, password,
                                  otp, self.key, timeout_ms, max_chars, allow_host)
            if err is not None:
                return err
            return await _do_cancel(page, confirm_texts, self.key, timeout_ms, max_chars)
        return await self._run(body)


class BrowserbaseBackend(_CDPConnectBackend):
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
        """Open a remote Browserbase session and return (pw, browser, page, None).

        Browserbase ends the session automatically when the CDP connection
        closes, so no explicit cleanup callable is needed.
        """
        import asyncio

        from playwright.async_api import async_playwright

        connect_url = await asyncio.to_thread(self._create_session)
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(connect_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, browser, page, None


class ApifyBackend(BrowserBackend):
    """Apify cloud scraping. Runs the ``apify/web-scraper`` Actor synchronously
    over the REST API and returns the page title + visible text.

    Apify executes the navigation entirely in its own cloud and does not hand a
    CDP session back to us, so this backend is intentionally non-interactive:
    only ``open_page`` is implemented. ``login_page`` / ``cancel_action`` fall
    through to the base "does not support" result, and the engine fails over to
    an interactive backend (local / Browserbase / AgentCore / Azure).

    Redirect safety: because the Actor runs remotely we cannot install the
    in-browser allow-list route interceptor the CDP backends use. We instead
    enforce ``allow_host`` at both observable ends — the requested URL's host is
    checked before the run, and the Actor reports the final (post-redirect) URL
    via ``page.url()``, whose host is re-checked before any content is returned.
    A run that lands off-list yields a ``blocked`` result with no page content,
    so an off-list redirect can never exfiltrate page data through this backend.

    Screenshots: when requested, the Actor's ``pageFunction`` captures a
    viewport screenshot with Puppeteer (``page.screenshot``) and returns it
    base64-encoded inside the dataset item, so no second key-value-store fetch is
    needed. The screenshot rides the same return path as the title/text, so the
    final-URL allow-list check gates it identically — an off-list redirect yields
    a blocked result with neither content nor screenshot. If the capture itself
    fails inside the Actor, the field comes back null and ``open_page`` still
    succeeds with ``screenshot_b64=None`` (degrade, don't fail the whole page).
    """

    API_BASE = "https://api.apify.com/v2"
    DEFAULT_ACTOR = "apify~web-scraper"

    def _available(self) -> bool:
        # requests is a core dependency; secrets_present() (the API token) is
        # checked by base.available().
        return True

    def _actor(self) -> str:
        return self.spec.settings.get("actor") or self.DEFAULT_ACTOR

    def _run_actor(self, url, timeout_ms, screenshot=True):
        """Blocking: run the scraper Actor for a single page and return its item.

        When ``screenshot`` is true the Actor captures a viewport PNG with
        Puppeteer and returns it base64-encoded in the dataset item's
        ``screenshot`` field. The capture is wrapped so a failure yields a null
        screenshot rather than aborting the page extraction.
        """
        import requests

        token = require_secret("APIFY_API_TOKEN")
        page_function = (
            "async function pageFunction(context) {"
            "  const { page, request, customData } = context;"
            "  const title = await page.title();"
            "  let text = '';"
            "  try {"
            "    text = await page.evaluate(() => document.body ? document.body.innerText : '');"
            "  } catch (e) {}"
            "  let screenshot = null;"
            "  if (customData && customData.screenshot) {"
            "    try {"
            "      screenshot = await page.screenshot({ type: 'png', encoding: 'base64', fullPage: false });"
            "    } catch (e) { screenshot = null; }"
            "  }"
            "  return { url: request.url, finalUrl: page.url(), title: title, text: text, screenshot: screenshot };"
            "}"
        )
        body = {
            "startUrls": [{"url": url}],
            "pageFunction": page_function,
            "customData": {"screenshot": bool(screenshot)},
            "proxyConfiguration": {"useApifyProxy": True},
            "maxRequestRetries": 1,
            "maxPagesPerCrawl": 1,
        }
        # Allow the synchronous Actor run a little longer than the page timeout
        # to cover cold-start; cap the HTTP read so we never hang forever.
        timeout_s = max(60, int(timeout_ms / 1000) + 30)
        r = requests.post(
            "%s/acts/%s/run-sync-get-dataset-items" % (self.API_BASE, self._actor()),
            headers={"Authorization": "Bearer %s" % token,
                     "Content-Type": "application/json"},
            json=body,
            timeout=timeout_s,
        )
        r.raise_for_status()
        items = r.json()
        return items[0] if isinstance(items, list) and items else {}

    async def open_page(self, url, *, timeout_ms=30000, screenshot=True, max_chars=4000, allow_host=None):
        import asyncio

        if allow_host is not None and not allow_host(_host_of(url)):
            return BrowserResult(
                ok=False, backend=self.key, url=url, final_url=url, blocked=True,
                error="Navigation blocked: '%s' is not in the browse allow-list."
                      % (_host_of(url) or url),
                attempts=[self.key],
            )

        item = await asyncio.to_thread(self._run_actor, url, timeout_ms, screenshot)
        final_url = item.get("finalUrl") or item.get("url") or url

        if allow_host is not None and not allow_host(_host_of(final_url)):
            return BrowserResult(
                ok=False, backend=self.key, url=url, final_url=final_url, blocked=True,
                error="Navigation blocked: final host '%s' is not in the browse "
                      "allow-list." % (_host_of(final_url) or final_url),
                attempts=[self.key],
            )

        if not item:
            return BrowserResult(
                ok=False, backend=self.key, url=url, final_url=final_url,
                error="Apify returned no result for this URL.",
                attempts=[self.key],
            )

        shot = item.get("screenshot") if screenshot else None
        if not isinstance(shot, str) or not shot:
            shot = None

        return BrowserResult(
            ok=True, backend=self.key, url=url, final_url=final_url,
            title=item.get("title"),
            text=(item.get("text") or "").strip()[:max_chars],
            screenshot_b64=shot,
            attempts=[self.key],
        )


class AgentCoreBackend(_CDPConnectBackend):
    """AWS Bedrock AgentCore Browser — a managed, isolated cloud Chromium reached
    over a SigV4-signed CDP WebSocket.

    Flow: start a browser session on the ``bedrock-agentcore`` data plane, build
    the automation-stream ``wss://`` URL, SigV4-sign the WebSocket upgrade with
    the caller's AWS credentials, drive it with Playwright over CDP (so every
    redirect-safety and interactive helper applies), then stop the session on
    teardown so we never leak a billed session.

    Region resolves from the backend ``settings.region`` then ``AWS_REGION`` /
    ``AWS_DEFAULT_REGION`` (default us-west-2); the browser identifier and
    session timeout are overridable via ``settings``. Credentials are read by
    boto3 from the environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, plus
    an optional session token) — never hand-handled here.
    """

    DEFAULT_IDENTIFIER = "aws.browser.v1"
    DEFAULT_REGION = "us-west-2"
    DEFAULT_SESSION_TIMEOUT = 3600

    def _available(self) -> bool:
        return _playwright_importable() and _boto3_importable()

    def _region(self) -> str:
        return (self.spec.settings.get("region")
                or get_env("AWS_REGION")
                or get_env("AWS_DEFAULT_REGION")
                or self.DEFAULT_REGION)

    def _identifier(self) -> str:
        return self.spec.settings.get("browser_identifier") or self.DEFAULT_IDENTIFIER

    def _session_timeout(self) -> int:
        try:
            return int(self.spec.settings.get("session_timeout_seconds")
                       or self.DEFAULT_SESSION_TIMEOUT)
        except (TypeError, ValueError):
            return self.DEFAULT_SESSION_TIMEOUT

    def _start_session(self):
        """Blocking: start a session, returning (ws_url, headers, stop_callable).

        Mirrors the official bedrock-agentcore SDK's session start + SigV4
        WebSocket signing, implemented directly on boto3/botocore so no extra
        SDK is required.
        """
        import base64 as _b64
        import datetime as _dt
        import secrets as _secrets
        import uuid as _uuid

        import boto3
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        region = self._region()
        client = boto3.client("bedrock-agentcore", region_name=region)
        resp = client.start_browser_session(
            browserIdentifier=self._identifier(),
            name="mydude-%s" % _uuid.uuid4().hex[:8],
            sessionTimeoutSeconds=self._session_timeout(),
        )
        identifier = resp["browserIdentifier"]
        session_id = resp["sessionId"]

        host = "bedrock-agentcore.%s.amazonaws.com" % region
        path = "/browser-streams/%s/sessions/%s/automation" % (identifier, session_id)
        ws_url = "wss://%s%s" % (host, path)

        creds = boto3.Session().get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials found for the AgentCore browser.")
        frozen = creds.get_frozen_credentials()
        amz_date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sign_req = AWSRequest(
            method="GET",
            url="https://%s%s" % (host, path),
            headers={"host": host, "x-amz-date": amz_date},
        )
        SigV4Auth(frozen, "bedrock-agentcore", region).add_auth(sign_req)
        headers = {
            "Host": host,
            "X-Amz-Date": sign_req.headers["x-amz-date"],
            "Authorization": sign_req.headers["Authorization"],
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": _b64.b64encode(_secrets.token_bytes(16)).decode(),
            "User-Agent": "MyDude-AgentCoreBrowser/1.0 (Session: %s)" % session_id,
        }
        if frozen.token:
            headers["X-Amz-Security-Token"] = frozen.token

        def stop():
            try:
                client.stop_browser_session(
                    browserIdentifier=identifier, sessionId=session_id
                )
            except Exception:
                pass

        return ws_url, headers, stop

    async def _connect(self):
        import asyncio

        from playwright.async_api import async_playwright

        ws_url, headers, stop = await asyncio.to_thread(self._start_session)
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(ws_url, headers=headers)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        async def cleanup():
            await asyncio.to_thread(stop)

        return pw, browser, page, cleanup


class AzureBackend(_CDPConnectBackend):
    """Azure Playwright Workspaces (formerly Microsoft Playwright Testing) — a
    managed cloud Chromium reached over a WebSocket.

    We connect Playwright to the workspace service URL (``PLAYWRIGHT_SERVICE_URL``)
    with the workspace access token as a Bearer header; the required
    ``api-version`` / ``os`` query parameters (and a per-run ``runId``) are
    appended. All redirect-safety and interactive helpers apply once connected.
    The token is read via the secrets layer — never hand-handled here.
    """

    DEFAULT_API_VERSION = "2024-12-01"
    DEFAULT_OS = "linux"

    def _available(self) -> bool:
        return _playwright_importable()

    def _ws_endpoint(self) -> str:
        from urllib.parse import urlencode, urlsplit
        import uuid as _uuid

        base = require_secret("PLAYWRIGHT_SERVICE_URL")
        params = {
            "api-version": self.spec.settings.get("api_version") or self.DEFAULT_API_VERSION,
            "os": self.spec.settings.get("os") or self.DEFAULT_OS,
            "runId": get_env("PLAYWRIGHT_SERVICE_RUN_ID") or ("mydude-%s" % _uuid.uuid4().hex[:8]),
        }
        sep = "&" if urlsplit(base).query else "?"
        return base + sep + urlencode(params)

    async def _connect(self):
        from playwright.async_api import async_playwright

        token = require_secret("AZURE_PLAYWRIGHT_ACCESS_TOKEN")
        ws_endpoint = self._ws_endpoint()
        pw = await async_playwright().start()
        browser = await pw.chromium.connect(
            ws_endpoint,
            headers={"Authorization": "Bearer %s" % token},
        )
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, browser, page, None
