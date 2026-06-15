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
        "amount": sub.amount,
        "cost_is_estimate": bool(sub.cost_is_estimate) if sub.est_cost else False,
        "currency": sub.currency or "",
        "cadence": sub.cadence or "",
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
    from src.subscriptions.spend import summarize_monthly_spend
    return {
        "request": request,
        "subscriptions": rows,
        "audit": audit,
        "catalog": all_services(),
        "spend": summarize_monthly_spend(rows),
        "result": result,
    }


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse("subscriptions.html", _context(request))


def _merge_sources(existing, incoming):
    """Union two ``+``-joined source attributions, preserving first-seen order.

    e.g. ``("browser_history", "email_receipt")`` -> ``"browser_history+email_receipt"``.
    A bare ``"discovery"`` placeholder is dropped once a real source is known.
    """
    tokens = []
    for blob in (existing, incoming):
        for tok in (blob or "").split("+"):
            tok = tok.strip()
            if tok and tok not in tokens:
                tokens.append(tok)
    real = [t for t in tokens if t != "discovery"]
    tokens = real or tokens
    return "+".join(tokens)


def _structured_cost(cand):
    """Derive ``(amount, currency, cadence)`` for a candidate.

    The receipt parser's explicit ``cadence`` (extracted from the email text)
    wins over whatever a catalog-default ``est_cost`` string happens to carry;
    amount and currency are parsed from the chosen ``est_cost``.
    """
    from src.subscriptions.discovery import parse_cost_string
    amount, currency, parsed_cadence = parse_cost_string(cand.get("est_cost"))
    return amount, currency, (cand.get("cadence") or parsed_cadence)


def backfill_structured_costs():
    """Best-effort one-time backfill of the structured cost columns from the
    legacy ``est_cost`` string for rows that predate the split.

    Only fills values that are still missing — the human-readable ``est_cost``
    is left untouched — so re-running it is a no-op. Returns the number of rows
    enriched. Safe to call at startup before any structured-cost reads.
    """
    from src.subscriptions.discovery import parse_cost_string
    enriched = 0
    db = SessionLocal()
    try:
        rows = (
            db.query(Subscription)
            .filter(
                Subscription.est_cost.isnot(None),
                (Subscription.amount.is_(None)) | (Subscription.cadence.is_(None)),
            )
            .all()
        )
        for sub in rows:
            amount, currency, cadence = parse_cost_string(sub.est_cost)
            changed = False
            if sub.amount is None and amount is not None:
                sub.amount = amount
                changed = True
            if not sub.currency and currency:
                sub.currency = currency
                changed = True
            if not sub.cadence and cadence:
                sub.cadence = cadence
                changed = True
            if changed:
                enriched += 1
        if enriched:
            db.commit()
    finally:
        db.close()
    return enriched


def _insert_candidates(candidates):
    """Insert discovered candidates as ``candidate`` rows, de-duped by domain.

    A candidate whose domain already exists (from any source) is *merged* into
    the existing row rather than dropped:

    * its source attribution is unioned into the existing one, so a service seen
      in both browser history and email receipts is recorded as
      ``browser_history+email_receipt`` (the "both" case);
    * any cost/cadence the receipt parser actually extracted backfills a row that
      had none — and upgrades a not-yet-confirmed candidate's catalog estimate —
      so the user can confirm it in one click without re-typing the amount;
    * missing login/account URLs are filled in.

    Returns ``(added, updated)`` — newly inserted vs. enriched existing rows.
    """
    added = 0
    updated = 0
    db = SessionLocal()
    try:
        for cand in candidates:
            domain = cand.get("domain")
            cand_source = cand.get("source") or "discovery"
            existing = (
                db.query(Subscription).filter(Subscription.domain == domain).first()
                if domain else None
            )
            cand_amount, cand_currency, cand_cadence = _structured_cost(cand)
            if existing:
                changed = False
                merged_source = _merge_sources(existing.source, cand_source)
                if merged_source != (existing.source or ""):
                    existing.source = merged_source
                    changed = True
                cand_cost = cand.get("est_cost")
                cand_from_receipt = bool(cand.get("cost_from_receipt"))
                # A real receipt amount beats a catalog guess: upgrade whenever
                # the existing cost is still an estimate (or absent), regardless
                # of confirmed/candidate status, so a stale default is never kept
                # once the user's actual amount is known. A bare catalog default
                # only backfills a row that has no cost at all. ``upgrade`` also
                # drives the structured amount/currency/cadence lockstep below.
                upgrade = bool(
                    cand_from_receipt and cand_cost
                    and (existing.cost_is_estimate or not existing.est_cost)
                    and cand_cost != existing.est_cost
                )
                if upgrade:
                    existing.est_cost = cand_cost
                    existing.cost_is_estimate = False
                    changed = True
                elif cand_cost and not existing.est_cost:
                    existing.est_cost = cand_cost
                    existing.cost_is_estimate = not cand_from_receipt
                    changed = True
                # Keep the structured columns in lockstep: overwrite on an
                # est_cost upgrade, otherwise only backfill what's still missing.
                if cand_amount is not None and (upgrade or existing.amount is None) \
                        and existing.amount != cand_amount:
                    existing.amount = cand_amount
                    changed = True
                if cand_currency and (upgrade or not existing.currency) \
                        and existing.currency != cand_currency:
                    existing.currency = cand_currency
                    changed = True
                if cand_cadence and (upgrade or not existing.cadence) \
                        and existing.cadence != cand_cadence:
                    existing.cadence = cand_cadence
                    changed = True
                for field in ("login_url", "account_url"):
                    if not getattr(existing, field) and cand.get(field):
                        setattr(existing, field, cand.get(field))
                        changed = True
                if changed:
                    updated += 1
                continue
            db.add(Subscription(
                name=cand["name"],
                domain=domain,
                login_url=cand.get("login_url"),
                account_url=cand.get("account_url"),
                est_cost=cand.get("est_cost"),
                amount=cand_amount,
                currency=cand_currency,
                cadence=cand_cadence,
                cost_is_estimate=not cand.get("cost_from_receipt"),
                status="candidate",
                source=cand_source,
            ))
            added += 1
        db.commit()
    finally:
        db.close()
    return added, updated


def _discover_result(message, candidates, added, updated=0):
    bits = []
    if added:
        bits.append("Added %d new candidate(s)." % added)
    if updated:
        bits.append("Enriched %d existing (merged source/cost)." % updated)
    if not bits and candidates:
        bits.append("No changes (already tracked).")
    suffix = (" " + " ".join(bits)) if bits else ""
    return {
        "kind": "discover",
        "ok": bool(candidates),
        "message": "%s%s" % (message, suffix),
    }


@router.post("/subscriptions/discover", response_class=HTMLResponse)
async def discover(request: Request, browser: str = Form("chrome"), _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_history

    broker = _broker()
    candidates, message = await discover_from_history(broker, browser=browser, limit=200)
    added, updated = _insert_candidates(candidates)
    result = _discover_result(message, candidates, added, updated)
    return templates.TemplateResponse("subscriptions.html", _context(request, result=result))


@router.post("/subscriptions/discover/email", response_class=HTMLResponse)
async def discover_email(request: Request, _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_email

    broker = _broker()
    candidates, message = await discover_from_email(broker, limit=50, lookback_days=365)
    added, updated = _insert_candidates(candidates)
    result = _discover_result(message, candidates, added, updated)
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
    # A cost the user typed is authoritative; a catalog fallback is an estimate.
    user_cost = est_cost.strip()
    est_cost_val = (user_cost or (entry.get("est_cost") if entry else None))
    amount, currency, cadence = _structured_cost({"est_cost": est_cost_val})
    db = SessionLocal()
    try:
        db.add(Subscription(
            name=name,
            domain=(domain.strip() or (entry["domains"][0] if entry else None)),
            login_url=(login_url.strip() or (entry["login_url"] if entry else None)),
            account_url=(account_url.strip() or (entry["account_url"] if entry else None)),
            login_username=(login_username.strip() or None),
            est_cost=est_cost_val,
            amount=amount,
            currency=currency,
            cadence=cadence,
            cost_is_estimate=not bool(user_cost),
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
