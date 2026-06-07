"""Subscriptions console — discover recurring subscriptions, then let MyDude log
in and (with an explicit confirmation gate) cancel them, all through the same
governed broker -> policy -> integrations path the swarm uses.

Discovery is best-effort inference from the user's browser history (read over the
SSH bridge) plus anything added manually. Nothing is acted on until the user
confirms a candidate, and no irreversible cancel step ever runs without a
two-phase request -> confirm handshake.
"""
import logging
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.database import SessionLocal
from src.models import Subscription, SubscriptionAction, ApiKey
from src.web.auth import require_auth
from src.web.crypto import encrypt_value
from src.web.templating import templates
from src.subscriptions import manager
from src.subscriptions.catalog import all_services, match_host

logger = logging.getLogger(__name__)
router = APIRouter()

_STATUS_ORDER = {
    "cancel_pending": 0,
    "confirmed": 1,
    "candidate": 2,
    "cancelled": 3,
    "dismissed": 4,
}


def _broker():
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine
    return CapabilityBroker(PolicyEngine(), Integrations())


def _serialize(sub):
    return {
        "id": sub.id,
        "name": sub.name,
        "domain": sub.domain or "",
        "login_url": sub.login_url or "",
        "account_url": sub.account_url or "",
        "login_username": sub.login_username or "",
        "has_credential": bool(sub.credential_key_id),
        "status": sub.status,
        "est_cost": sub.est_cost or "",
        "currency": sub.currency or "",
        "source": sub.source or "",
        "notes": sub.notes or "",
        "last_checked_at": sub.last_checked_at,
        "created_at": sub.created_at,
    }


def _context(request, result=None):
    db = SessionLocal()
    try:
        subs = db.query(Subscription).all()
        subs_sorted = sorted(
            subs,
            key=lambda s: (_STATUS_ORDER.get(s.status, 9), (s.name or "").lower()),
        )
        rows = [_serialize(s) for s in subs_sorted]
        actions = (
            db.query(SubscriptionAction)
            .order_by(SubscriptionAction.created_at.desc())
            .limit(40)
            .all()
        )
        name_by_id = {s.id: s.name for s in subs}
        audit = [{
            "subscription": name_by_id.get(a.subscription_id, "#%s" % a.subscription_id),
            "action": a.action,
            "status": a.status,
            "detail": a.detail or "",
            "created_at": a.created_at,
        } for a in actions]
    finally:
        db.close()
    return {
        "request": request,
        "subscriptions": rows,
        "audit": audit,
        "catalog": all_services(),
        "result": result,
    }


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse("subscriptions.html", _context(request))


def _insert_candidates(candidates):
    """Insert discovered candidates as ``candidate`` rows, de-duped by domain.

    A candidate whose domain already exists (from any source) is skipped, so the
    two discovery sources naturally merge into one tracked set. Returns the count
    of newly-added rows.
    """
    added = 0
    db = SessionLocal()
    try:
        for cand in candidates:
            domain = cand.get("domain")
            if domain:
                exists = (
                    db.query(Subscription)
                    .filter(Subscription.domain == domain)
                    .first()
                )
                if exists:
                    continue
            db.add(Subscription(
                name=cand["name"],
                domain=domain,
                login_url=cand.get("login_url"),
                account_url=cand.get("account_url"),
                est_cost=cand.get("est_cost"),
                status="candidate",
                source=cand.get("source") or "discovery",
            ))
            added += 1
        db.commit()
    finally:
        db.close()
    return added


def _discover_result(message, candidates, added):
    return {
        "kind": "discover",
        "ok": bool(candidates),
        "message": "%s%s" % (
            message,
            (" Added %d new candidate(s)." % added) if added else
            (" No new candidates (already tracked)." if candidates else ""),
        ),
    }


@router.post("/subscriptions/discover", response_class=HTMLResponse)
async def discover(request: Request, browser: str = Form("chrome"), _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_history

    broker = _broker()
    candidates, message = await discover_from_history(broker, browser=browser, limit=200)
    added = _insert_candidates(candidates)
    result = _discover_result(message, candidates, added)
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))


@router.post("/subscriptions/discover/email", response_class=HTMLResponse)
async def discover_email(request: Request, _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_email

    broker = _broker()
    candidates, message = await discover_from_email(broker, limit=50, lookback_days=365)
    added = _insert_candidates(candidates)
    result = _discover_result(message, candidates, added)
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))


@router.post("/subscriptions/add")
async def add_manual(
    request: Request,
    name: str = Form(""),
    domain: str = Form(""),
    login_url: str = Form(""),
    account_url: str = Form(""),
    login_username: str = Form(""),
    est_cost: str = Form(""),
    notes: str = Form(""),
    _=Depends(require_auth),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/subscriptions?err=" + quote("A name is required."), status_code=303)

    # Fill in known URLs from the catalog when the user only gives a domain.
    entry = match_host(domain.strip()) if domain.strip() else None
    db = SessionLocal()
    try:
        db.add(Subscription(
            name=name,
            domain=(domain.strip() or (entry["domains"][0] if entry else None)),
            login_url=(login_url.strip() or (entry["login_url"] if entry else None)),
            account_url=(account_url.strip() or (entry["account_url"] if entry else None)),
            login_username=(login_username.strip() or None),
            est_cost=(est_cost.strip() or (entry.get("est_cost") if entry else None)),
            notes=(notes.strip() or None),
            status="confirmed",
            source="manual",
        ))
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/subscriptions?msg=" + quote("Subscription added."), status_code=303)


@router.post("/subscriptions/{sub_id}/status")
async def set_status(request: Request, sub_id: int, status: str = Form(""), _=Depends(require_auth)):
    status = status.strip()
    if status not in ("confirmed", "dismissed", "candidate"):
        return RedirectResponse("/subscriptions?err=" + quote("Invalid status."), status_code=303)
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if not sub:
            return RedirectResponse("/subscriptions?err=" + quote("Not found."), status_code=303)
        sub.status = status
        db.add(SubscriptionAction(
            subscription_id=sub.id, action="status_change", status="ok", detail="-> %s" % status
        ))
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/subscriptions?msg=" + quote("Status updated."), status_code=303)


@router.post("/subscriptions/{sub_id}/credentials")
async def set_credentials(
    request: Request,
    sub_id: int,
    login_url: str = Form(""),
    account_url: str = Form(""),
    login_username: str = Form(""),
    password: str = Form(""),
    _=Depends(require_auth),
):
    """Store login URLs/username, and (if provided) the account password.

    The password is encrypted into the vault as a dedicated ApiKey row and
    referenced by id — it is never stored in plaintext on the subscription.
    """
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if not sub:
            return RedirectResponse("/subscriptions?err=" + quote("Not found."), status_code=303)
        if login_url.strip():
            sub.login_url = login_url.strip()
        if account_url.strip():
            sub.account_url = account_url.strip()
        sub.login_username = login_username.strip() or None
        if password:
            provider = "subscription:%s" % sub.id
            key = db.query(ApiKey).filter(ApiKey.provider == provider).first()
            if key:
                key.encrypted_key = encrypt_value(password)
                key.is_active = True
                key.last_rotated_at = datetime.utcnow()
            else:
                key = ApiKey(
                    provider=provider,
                    label="%s login" % sub.name,
                    encrypted_key=encrypt_value(password),
                    is_active=True,
                    category="Subscription logins",
                    last_rotated_at=datetime.utcnow(),
                )
                db.add(key)
                db.flush()
            sub.credential_key_id = key.id
        db.add(SubscriptionAction(
            subscription_id=sub.id, action="credentials_saved", status="ok",
            detail="password %s" % ("updated" if password else "unchanged"),
        ))
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/subscriptions?msg=" + quote("Credentials saved."), status_code=303)


@router.post("/subscriptions/{sub_id}/delete")
async def delete_sub(request: Request, sub_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if sub:
            if sub.credential_key_id:
                key = db.query(ApiKey).filter(ApiKey.id == sub.credential_key_id).first()
                if key:
                    db.delete(key)
            db.query(SubscriptionAction).filter(
                SubscriptionAction.subscription_id == sub_id
            ).delete()
            db.delete(sub)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/subscriptions?msg=" + quote("Subscription removed."), status_code=303)


@router.post("/subscriptions/{sub_id}/open", response_class=HTMLResponse)
async def open_account(request: Request, sub_id: int, _=Depends(require_auth)):
    res = await manager.open_account(sub_id)
    result = {"kind": "open", "ok": res.get("ok"), "message": res.get("message"),
              "screenshot": res.get("screenshot"), "sub_id": sub_id}
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))


@router.post("/subscriptions/{sub_id}/cancel/request", response_class=HTMLResponse)
async def cancel_request(request: Request, sub_id: int, _=Depends(require_auth)):
    res = await manager.request_cancel(sub_id)
    result = {"kind": "cancel_request", "ok": res.get("ok"), "message": res.get("message"),
              "screenshot": res.get("screenshot"), "pending": res.get("pending"), "sub_id": sub_id}
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))


@router.post("/subscriptions/{sub_id}/cancel/confirm", response_class=HTMLResponse)
async def cancel_confirm(
    request: Request,
    sub_id: int,
    confirm: str = Form(""),
    _=Depends(require_auth),
):
    # Mandatory explicit confirmation gate: the form posts the literal word
    # CANCEL. Without it we refuse and never reach the irreversible step.
    if confirm.strip().upper() != "CANCEL":
        result = {"kind": "cancel_confirm", "ok": False, "sub_id": sub_id,
                  "message": "Confirmation text did not match — type CANCEL to confirm. "
                             "Nothing was cancelled."}
        return templates.TemplateResponse("subscriptions.html", _context(request, result=result))
    res = await manager.confirm_cancel(sub_id)
    result = {"kind": "cancel_confirm", "ok": res.get("ok"), "message": res.get("message"),
              "screenshot": res.get("screenshot"), "sub_id": sub_id}
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))
