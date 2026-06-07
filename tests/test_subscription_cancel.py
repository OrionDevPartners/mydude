"""Tests for the cancel heuristics and the two-phase cancellation gate.

These cover, offline and with no real browser or credentials:

  1. ``_do_cancel`` heuristics in ``src/browser/backends.py``: it walks a
     multi-step cancel/confirm flow, finds controls styled as links or
     ``role=button`` (not just real ``<button>``), waits for the SPA account
     page to settle before scanning, and reports honestly when no cancel
     control exists.
  2. ``DEFAULT_CANCEL_TEXTS`` covers the documented cancel labels of the
     cataloged providers (Netflix/Spotify/Amazon/Disney+/Hulu/YouTube).
  3. The two-phase gate in ``src/subscriptions/manager.py``:
       * ``confirm_cancel`` refuses unless the record is ``cancel_pending``.
       * ``request_cancel`` always moves to ``cancel_pending`` and surfaces the
         confirmation gate even when the best-effort review login is blocked
         (the gate is decoupled from login success).
       * ``request_cancel`` → ``confirm_cancel`` cancels on success.
       * a blocked irreversible step leaves the record ``cancel_pending`` (not
         ``cancelled``) so the user can retry.

The page, the DB session, and the capability broker are all faked, so the
suite runs offline.

Runnable two ways:
  * ``python tests/test_subscription_cancel.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_subscription_cancel.py``   (test_* functions; no plugins needed)
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.browser import backends as backends_mod
from src.browser.backends import DEFAULT_CANCEL_TEXTS, _do_cancel
from src.subscriptions import manager as mgr


# -- fake page for _do_cancel -------------------------------------------------

class _Btn:
    def __init__(self, text, role="button", visible=True):
        self.text = text
        self.role = role
        self.visible = visible


class _FakeElement:
    def __init__(self, page, btn):
        self._page = page
        self._btn = btn

    async def is_visible(self):
        return bool(self._btn and self._btn.visible)

    async def inner_text(self):
        return self._btn.text if self._btn else ""

    async def text_content(self):
        return self._btn.text if self._btn else ""

    async def click(self):
        self._page._advance(self._btn.text)


class _FakeLocator:
    def __init__(self, page, btns):
        self._page = page
        self._btns = btns

    async def count(self):
        return len(self._btns)

    @property
    def first(self):
        return _FakeElement(self._page, self._btns[0] if self._btns else None)

    def nth(self, i):
        return _FakeElement(self._page, self._btns[i] if 0 <= i < len(self._btns) else None)


class _FakeQuery:
    def __init__(self, page):
        self._page = page

    def filter(self, has_text=None):
        needle = (has_text or "").lower()
        btns = [b for b in self._page._current()
                if b.visible and needle in b.text.lower()]
        return _FakeLocator(self._page, btns)


class _FakeCancelPage:
    """A fake page modeling a step-by-step cancel flow.

    ``steps`` is a list of button lists. Clicking a matching control on the
    current step advances to the next step (mimicking a multi-page cancel flow).
    """

    def __init__(self, steps):
        self.steps = steps
        self.idx = 0
        self.url = "https://www.example.com/account"
        self.clicks = []
        self.load_state_waits = 0
        self.selector_waits = 0

    def _current(self):
        return self.steps[self.idx] if self.idx < len(self.steps) else []

    def _advance(self, label):
        self.clicks.append(label)
        if self.idx < len(self.steps):
            self.idx += 1

    async def wait_for_load_state(self, *a, **k):
        self.load_state_waits += 1

    async def wait_for_selector(self, *a, **k):
        self.selector_waits += 1
        if not any(b.visible for b in self._current()):
            raise RuntimeError("nothing visible yet")
        return True

    async def wait_for_timeout(self, *a, **k):
        return None

    def get_by_role(self, role, name=None, exact=False):
        needle = (name or "").lower()
        btns = [b for b in self._current()
                if b.role == role and b.visible and needle in b.text.lower()]
        return _FakeLocator(self, btns)

    def locator(self, selector):
        return _FakeQuery(self)

    async def title(self):
        return "Account"

    async def inner_text(self, selector):
        return "account body"

    async def screenshot(self, **k):
        return b"\x89PNG"


def test_do_cancel_walks_multi_step_button_flow():
    page = _FakeCancelPage([
        [_Btn("Cancel Membership")],
        [_Btn("Finish Cancellation")],
        [],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert res.ok, res.error
    assert page.clicks == ["Cancel Membership", "Finish Cancellation"], page.clicks
    assert page.load_state_waits >= 1, "should let the SPA settle before scanning"


def test_do_cancel_finds_link_styled_control_via_fallback():
    # A cancel control rendered as an <a>/role=link is invisible to
    # get_by_role("button", ...) but must be found by the a/button/[role=button]
    # fallback locator.
    page = _FakeCancelPage([
        [_Btn("Cancel your subscription", role="link")],
        [_Btn("Yes, cancel", role="link")],
        [],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert res.ok, res.error
    assert page.clicks == ["Cancel your subscription", "Yes, cancel"], page.clicks


def test_do_cancel_honest_when_no_control_found():
    page = _FakeCancelPage([
        [_Btn("Update payment method"), _Btn("Change plan")],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert not res.ok
    assert res.error and "cancel control" in res.error.lower(), res.error
    assert page.clicks == [], page.clicks


def test_do_cancel_declines_retention_interstitial():
    # initiate -> "pause instead" retention screen -> "are you sure?" -> done.
    # On both interstitials the prominent control keeps you subscribed; the
    # walker must take the decline path and reach the real confirm.
    page = _FakeCancelPage([
        [_Btn("Cancel membership")],
        # Retention "pause instead" / discount upsell.
        [_Btn("Keep my membership"),
         _Btn("Pause membership instead"),
         _Btn("No thanks, continue to cancel")],
        # Extra "are you sure?" interstitial.
        [_Btn("Stay subscribed"),
         _Btn("Confirm cancellation")],
        [],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert res.ok, res.error
    assert page.clicks == [
        "Cancel membership",
        "No thanks, continue to cancel",
        "Confirm cancellation",
    ], page.clicks


def test_do_cancel_never_clicks_keep_even_when_label_substring_matches():
    # The prominent retention button's text *contains* a cancel label
    # ("Cancel anyway? ...") yet is really a KEEP control. The walker must skip
    # it via the keep-guard and click the genuine decline control beside it.
    page = _FakeCancelPage([
        [_Btn("Cancel membership")],
        [_Btn("Cancel anyway? No — keep my plan"),
         _Btn("Cancel anyway")],
        [],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert res.ok, res.error
    assert page.clicks == ["Cancel membership", "Cancel anyway"], page.clicks
    assert "Cancel anyway? No — keep my plan" not in page.clicks


def test_do_cancel_will_not_accept_retention_offer_when_only_keep_controls():
    # When the only controls left keep you subscribed (no decline path), the
    # walker must NOT click any of them; it stops rather than staying subscribed.
    page = _FakeCancelPage([
        [_Btn("Cancel membership")],
        [_Btn("Keep my plan"),
         _Btn("Pause membership instead"),
         _Btn("Get the discount")],
    ])
    res = asyncio.run(_do_cancel(page, None, "browserbase", 45000, 4000))
    assert page.clicks == ["Cancel membership"], page.clicks
    for keep in ("Keep my plan", "Pause membership instead", "Get the discount"):
        assert keep not in page.clicks


def test_default_cancel_texts_cover_retention_decline_controls():
    lower = [t.lower() for t in DEFAULT_CANCEL_TEXTS]

    def covered(label):
        l = label.lower()
        return any(t in l or l in t for t in lower)

    for label in [
        "No thanks",
        "No thanks, continue to cancel",
        "Continue canceling",
        "Decline offer",
    ]:
        assert covered(label), "DEFAULT_CANCEL_TEXTS missing decline coverage for %r" % label


def test_looks_like_keep_flags_retention_controls():
    for keep in [
        "Keep my plan", "Pause membership instead", "Stay subscribed",
        "Get the discount", "Don't cancel", "Remind me later",
        "No, keep my membership",
    ]:
        assert backends_mod._looks_like_keep(keep), keep
    for go in [
        "Cancel membership", "No thanks, continue to cancel",
        "Confirm cancellation", "Continue to cancel", "Yes, cancel",
    ]:
        assert not backends_mod._looks_like_keep(go), go


def test_default_cancel_texts_cover_known_providers():
    lower = [t.lower() for t in DEFAULT_CANCEL_TEXTS]

    def covered(label):
        l = label.lower()
        return any(t in l or l in t for t in lower)

    # Representative labels observed on the cataloged providers' cancel flows.
    for label in [
        "Cancel Membership",       # Netflix initiate
        "Finish Cancellation",     # Netflix confirm
        "Cancel Premium",          # Spotify initiate
        "Continue to cancel",      # Spotify/Hulu progress
        "End Membership",          # Amazon Prime
        "End my benefits",         # Amazon Prime confirm
        "Complete Cancellation",   # Disney+ confirm
        "Cancel your subscription",  # Hulu initiate
        "Yes, cancel",             # generic confirm
    ]:
        assert covered(label), "DEFAULT_CANCEL_TEXTS missing coverage for %r" % label


# -- two-phase gate (faked DB + broker) ---------------------------------------

class _FakeSub:
    def __init__(self, status="confirmed"):
        self.id = 1
        self.status = status
        self.credential_key_id = 5
        self.login_url = "https://www.example.com/login"
        self.account_url = "https://www.example.com/account"
        self.login_username = "user@example.com"
        self.domain = None
        self.last_checked_at = None


class _FakeDB:
    def __init__(self, sub):
        self._sub = sub
        self.added = []

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._sub

    def add(self, row):
        self.added.append(row)

    def commit(self):
        return None

    def close(self):
        return None


class _Decision:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason


class _Res:
    def __init__(self, allowed=True, output="", reason="", screenshot_b64=None):
        self.decision = _Decision(allowed, reason)
        self.output = output
        self.screenshot_b64 = screenshot_b64


class _FakeBroker:
    def __init__(self, res):
        self._res = res

    async def request(self, name, params):
        return self._res


def _patch_manager(sub, broker_res):
    """Patch the manager's DB/broker/credential seams; return a restore fn."""
    saved = (mgr.SessionLocal, mgr._broker, mgr._credential, mgr._maybe_fetch_otp)
    db = _FakeDB(sub)

    async def _fake_otp(broker):
        return None

    mgr.SessionLocal = lambda: db
    mgr._broker = lambda: _FakeBroker(broker_res)
    mgr._credential = lambda d, s: ("secret-pw", None)
    mgr._maybe_fetch_otp = _fake_otp

    def restore():
        (mgr.SessionLocal, mgr._broker, mgr._credential,
         mgr._maybe_fetch_otp) = saved

    return restore


def test_confirm_cancel_refuses_without_pending():
    sub = _FakeSub(status="confirmed")
    restore = _patch_manager(sub, _Res(allowed=True, output="browser_cancel ok"))
    try:
        res = asyncio.run(mgr.confirm_cancel(1))
    finally:
        restore()
    assert not res["ok"]
    assert "request" in res["message"].lower()
    assert sub.status == "confirmed", "irreversible step must not run un-gated"


def test_request_cancel_surfaces_gate_even_when_login_blocked():
    sub = _FakeSub(status="confirmed")
    # Review login is policy-blocked; the gate must still appear.
    restore = _patch_manager(sub, _Res(allowed=False, reason="Browser capability disabled"))
    try:
        res = asyncio.run(mgr.request_cancel(1))
    finally:
        restore()
    assert res["pending"] is True, "confirmation gate must surface regardless of login"
    assert sub.status == "cancel_pending"


def test_request_then_confirm_cancels_on_success():
    sub = _FakeSub(status="confirmed")
    restore = _patch_manager(sub, _Res(allowed=True, output="browser_login ok — account"))
    try:
        req = asyncio.run(mgr.request_cancel(1))
    finally:
        restore()
    assert req["pending"] is True
    assert sub.status == "cancel_pending"

    restore = _patch_manager(sub, _Res(allowed=True, output="browser_cancel ok — Clicked: Cancel membership"))
    try:
        conf = asyncio.run(mgr.confirm_cancel(1))
    finally:
        restore()
    assert conf["ok"], conf["message"]
    assert sub.status == "cancelled"


def test_confirm_cancel_blocked_keeps_pending():
    sub = _FakeSub(status="cancel_pending")
    restore = _patch_manager(sub, _Res(allowed=False, reason="Browser capability disabled"))
    try:
        res = asyncio.run(mgr.confirm_cancel(1))
    finally:
        restore()
    assert not res["ok"]
    assert sub.status == "cancel_pending", "a blocked cancel must stay pending for retry"


def _run_all():
    tests = [
        test_do_cancel_walks_multi_step_button_flow,
        test_do_cancel_finds_link_styled_control_via_fallback,
        test_do_cancel_honest_when_no_control_found,
        test_do_cancel_declines_retention_interstitial,
        test_do_cancel_never_clicks_keep_even_when_label_substring_matches,
        test_do_cancel_will_not_accept_retention_offer_when_only_keep_controls,
        test_default_cancel_texts_cover_retention_decline_controls,
        test_looks_like_keep_flags_retention_controls,
        test_default_cancel_texts_cover_known_providers,
        test_confirm_cancel_refuses_without_pending,
        test_request_cancel_surfaces_gate_even_when_login_blocked,
        test_request_then_confirm_cancels_on_success,
        test_confirm_cancel_blocked_keeps_pending,
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
