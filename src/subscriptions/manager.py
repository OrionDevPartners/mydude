"""Subscription action manager — the governed bridge between the UI and the
browser capability for logging in and cancelling subscriptions.

Cancellation is deliberately two-phase:

1. :func:`request_cancel` logs in and navigates to the account/billing page so
   the user can *see* the current state. It performs NO irreversible action and
   flips the subscription to ``cancel_pending``.
2. :func:`confirm_cancel` is the only path that runs the irreversible cancel
   clicks, and it refuses to run unless the subscription is already in
   ``cancel_pending`` (i.e. the user has explicitly confirmed in the UI).

Every step pulls the password from the encrypted vault at call time (never
stored in plaintext on the subscription), optionally fetches a one-time SMS code
via the SSH bridge, and records a :class:`SubscriptionAction` audit row. Secrets
are never written to the audit trail.
"""
import logging
from datetime import datetime

from src.database import SessionLocal
from src.models import Subscription, SubscriptionAction, ApiKey
from src.web.crypto import decrypt_value

logger = logging.getLogger(__name__)


def _broker():
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine
    return CapabilityBroker(PolicyEngine(), Integrations())


def _record(db, subscription_id, action, status, detail=None):
    db.add(SubscriptionAction(
        subscription_id=subscription_id,
        action=action,
        status=status,
        detail=(str(detail)[:2000] if detail is not None else None),
    ))
    db.commit()


def _credential(db, sub):
    """Decrypt the stored account password for ``sub`` from the vault.

    Returns ``(password, error)``. ``error`` is a user-facing string when the
    credential is missing or undecryptable; ``password`` is None in that case.
    The plaintext is only ever returned to the caller for immediate use — it is
    never logged or persisted.
    """
    if not sub.credential_key_id:
        return None, ("No password is stored for this subscription yet. Add the "
                      "account password before MyDude can log in.")
    key = db.query(ApiKey).filter(ApiKey.id == sub.credential_key_id).first()
    if not key:
        return None, "The stored credential could not be found in the vault."
    try:
        return decrypt_value(key.encrypted_key), None
    except Exception as e:
        logger.warning("Failed to decrypt subscription credential: %s", e)
        return None, "The stored credential could not be decrypted."


def _code_from_output(out):
    """Pull the first OTP-looking digit run from a bridge's output string."""
    out = (out or "").strip()
    if not out or out.lower().startswith("no "):
        return None
    import re
    m = re.search(r"\b(\d{4,8})\b", out)
    return m.group(1) if m else None


async def _fetch_sms_code(broker):
    """Best-effort: pull a recent SMS code from the Mac via the SSH bridge."""
    try:
        res = await broker.request("ssh_fetch_code", {"source": "subscriptions-ui"})
    except Exception:
        return None
    if not res.decision.allowed:
        return None
    out = (res.output or "").strip()
    if out.startswith("SSH bridge error"):
        return None
    return _code_from_output(out)


async def _fetch_gmail_code(broker):
    """Best-effort: pull a recent emailed code from a connected Gmail account."""
    try:
        res = await broker.request("gmail_fetch_code", {"source": "subscriptions-ui"})
    except Exception:
        return None
    if not res.decision.allowed:
        return None
    out = (res.output or "").strip()
    if out.lower().startswith("gmail bridge error"):
        return None
    return _code_from_output(out)


async def _maybe_fetch_otp(broker):
    """Best-effort: pull a recent one-time code, trying SMS then emailed codes.

    Tries the SMS bridge (texts read from the Mac) first, then a connected Gmail
    for services that email the code instead. Returns the code string or None.
    A None here is honest — authenticator-app codes can't be read — not a silent
    failure: the caller still surfaces a "needs you" when nothing is found.
    """
    code = await _fetch_sms_code(broker)
    if code:
        return code
    return await _fetch_gmail_code(broker)


# Only an actively-tracked subscription may drive a browser login/cancel. A
# candidate (unconfirmed inference), a dismissed entry, or an already-cancelled
# one must never trigger an automated sign-in — that is enforced here in the
# manager, not just by which buttons the template renders.
_ACTIONABLE_STATUSES = ("confirmed", "cancel_pending")


def _guard_actionable(db, sub, action):
    """Return a user-facing error string if ``sub`` may not drive a browser
    action right now, else None. Records a blocked audit row on refusal."""
    if sub.status not in _ACTIONABLE_STATUSES:
        msg = ("This subscription is '%s'; confirm it first before MyDude signs "
               "in. Browser actions are only allowed for confirmed (or "
               "cancel-pending) subscriptions." % sub.status)
        _record(db, sub.id, action, "blocked", msg)
        return msg
    return None


def _urls_for(sub):
    """Resolve login/account URLs, preferring explicit values, then the catalog."""
    login_url = (sub.login_url or "").strip()
    account_url = (sub.account_url or "").strip()
    entry = None
    if sub.domain:
        from src.subscriptions.catalog import match_host
        entry = match_host(sub.domain)
    if not login_url and entry:
        login_url = entry["login_url"]
    if not account_url and entry:
        account_url = entry["account_url"]
    return login_url, account_url


async def open_account(subscription_id):
    """Log in and show the account/billing page (read-only). No cancellation."""
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            return {"ok": False, "message": "Subscription not found."}
        guard = _guard_actionable(db, sub, "open_account")
        if guard:
            return {"ok": False, "message": guard}
        password, err = _credential(db, sub)
        if err:
            _record(db, sub.id, "open_account", "error", err)
            return {"ok": False, "message": err}
        login_url, account_url = _urls_for(sub)
        if not login_url:
            msg = "No login URL is configured for this subscription."
            _record(db, sub.id, "open_account", "error", msg)
            return {"ok": False, "message": msg}

        broker = _broker()
        otp = await _maybe_fetch_otp(broker)
        res = await broker.request("browser_login", {
            "login_url": login_url,
            "account_url": account_url,
            "username": sub.login_username or "",
            "password": password,
            "otp": otp,
            "source": "subscriptions-ui",
        })
        if not res.decision.allowed:
            _record(db, sub.id, "open_account", "blocked", res.decision.reason)
            return {"ok": False, "message": res.decision.reason}
        ok = bool(res.output) and res.output.startswith("browser_login ok")
        sub.last_checked_at = datetime.utcnow()
        db.commit()
        _record(db, sub.id, "open_account", "ok" if ok else "needs_user", res.output)
        return {"ok": ok, "message": res.output, "screenshot": res.screenshot_b64}
    finally:
        db.close()


async def request_cancel(subscription_id):
    """Phase 1: log in and reach the cancel/account page WITHOUT cancelling.

    Marks the subscription ``cancel_pending`` so the UI can present an explicit
    confirmation. Never performs an irreversible action.
    """
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            return {"ok": False, "message": "Subscription not found."}
        guard = _guard_actionable(db, sub, "cancel_requested")
        if guard:
            return {"ok": False, "message": guard}
        password, err = _credential(db, sub)
        if err:
            _record(db, sub.id, "cancel_requested", "error", err)
            return {"ok": False, "message": err}
        login_url, account_url = _urls_for(sub)
        if not login_url:
            msg = "No login URL is configured for this subscription."
            _record(db, sub.id, "cancel_requested", "error", msg)
            return {"ok": False, "message": msg}

        broker = _broker()
        otp = await _maybe_fetch_otp(broker)
        res = await broker.request("browser_login", {
            "login_url": login_url,
            "account_url": account_url,
            "username": sub.login_username or "",
            "password": password,
            "otp": otp,
            "source": "subscriptions-ui",
        })
        # The explicit-confirmation gate is the safety mechanism, so it is shown
        # whenever a cancel is requested — independent of whether the best-effort
        # review login succeeded. The login outcome is reported honestly so the
        # user knows whether MyDude could actually reach the account page. The
        # irreversible step in confirm_cancel re-checks policy and refuses unless
        # status is cancel_pending, so surfacing the gate here is safe.
        ok = bool(res.output) and res.output.startswith("browser_login ok")
        review = res.decision.reason if not res.decision.allowed else (res.output or "")
        sub.status = "cancel_pending"
        sub.last_checked_at = datetime.utcnow()
        db.commit()
        _record(db, sub.id, "cancel_requested", "pending_confirm",
                "Awaiting explicit confirmation. Review login: %s" % review)
        return {
            "ok": ok,
            "message": review,
            "screenshot": res.screenshot_b64,
            "pending": True,
        }
    finally:
        db.close()


async def confirm_cancel(subscription_id):
    """Phase 2: the IRREVERSIBLE cancel. Only runs after an explicit confirm.

    Refuses unless the subscription is in ``cancel_pending`` (set by
    :func:`request_cancel`), so a stray POST can't trigger a cancellation.
    """
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            return {"ok": False, "message": "Subscription not found."}
        if sub.status != "cancel_pending":
            msg = ("Cancellation must be requested first. Use 'Request cancel' so "
                   "you can review the account page before confirming.")
            _record(db, sub.id, "cancel_confirmed", "error", msg)
            return {"ok": False, "message": msg}
        password, err = _credential(db, sub)
        if err:
            _record(db, sub.id, "cancel_confirmed", "error", err)
            return {"ok": False, "message": err}
        login_url, account_url = _urls_for(sub)
        if not login_url:
            msg = "No login URL is configured for this subscription."
            _record(db, sub.id, "cancel_confirmed", "error", msg)
            return {"ok": False, "message": msg}

        broker = _broker()
        otp = await _maybe_fetch_otp(broker)
        res = await broker.request("browser_cancel", {
            "login_url": login_url,
            "account_url": account_url,
            "username": sub.login_username or "",
            "password": password,
            "otp": otp,
            "source": "subscriptions-ui",
        })
        if not res.decision.allowed:
            _record(db, sub.id, "cancel_confirmed", "blocked", res.decision.reason)
            return {"ok": False, "message": res.decision.reason}
        ok = bool(res.output) and res.output.startswith("browser_cancel ok")
        if ok:
            sub.status = "cancelled"
        sub.last_checked_at = datetime.utcnow()
        db.commit()
        _record(db, sub.id, "cancel_confirmed", "ok" if ok else "needs_user", res.output)
        return {"ok": ok, "message": res.output, "screenshot": res.screenshot_b64}
    finally:
        db.close()
