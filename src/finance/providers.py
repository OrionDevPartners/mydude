"""Credential sourcing + connection status for finance providers.

Sourcing order for both QuickBooks and Plaid:
  1. Replit connector proxy (fetched fresh every call, never cached)
  2. Vault / environment variables (fallback; may go stale)
  3. Neither configured -> ``FinanceNotConfigured`` (fail loud, no mock data)

Status functions never raise — they report honestly so the UI can tell the
operator exactly what to connect. Credential functions raise when unconfigured.
"""
import os
import logging

from src.web.connectors import get_connection_settings
from src.web.crypto import (
    encrypt_value, decrypt_value, encryption_key_is_persistent,
)

logger = logging.getLogger(__name__)


class FinanceNotConfigured(RuntimeError):
    """Raised when a provider has no usable credentials."""


class FinanceAuthError(RuntimeError):
    """Raised when a provider rejects our credentials (expired / invalid)."""


class FinanceProviderError(RuntimeError):
    """Raised on a non-auth provider/API failure."""


_QBO_BASE = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}
_PLAID_BASE = {
    "production": "https://production.plaid.com",
    "development": "https://development.plaid.com",
    "sandbox": "https://sandbox.plaid.com",
}


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return None


# --------------------------------------------------------------------------- #
# QuickBooks Online
# --------------------------------------------------------------------------- #

def quickbooks_credentials():
    """Return ``{access_token, realm_id, base_url, source}`` or raise.

    Connector proxy first (refreshes tokens automatically), then env fallback.
    QuickBooks access tokens expire ~1h; the env fallback WILL go stale — callers
    map provider 401s to an actionable reconnect message.
    """
    env_name = (os.environ.get("QUICKBOOKS_ENV") or "production").strip().lower()
    base_url = _QBO_BASE.get(env_name, _QBO_BASE["production"])

    settings = get_connection_settings("quickbooks")
    if settings:
        token = (
            settings.get("access_token")
            or ((settings.get("oauth") or {}).get("credentials") or {}).get("access_token")
        )
        realm = (
            settings.get("realmId")
            or settings.get("realm_id")
            or settings.get("companyId")
            or settings.get("company_id")
            or _env("QUICKBOOKS_REALM_ID", "QBO_REALM_ID")
        )
        if token and realm:
            return {"access_token": token, "realm_id": realm,
                    "base_url": base_url, "source": "connector"}

    token = _env("QUICKBOOKS_ACCESS_TOKEN", "QBO_ACCESS_TOKEN")
    realm = _env("QUICKBOOKS_REALM_ID", "QBO_REALM_ID")
    if token and realm:
        return {"access_token": token, "realm_id": realm,
                "base_url": base_url, "source": "vault"}

    raise FinanceNotConfigured(
        "QuickBooks is not connected. Connect QuickBooks Online via Connected "
        "Services, or add QUICKBOOKS_ACCESS_TOKEN and QUICKBOOKS_REALM_ID to the "
        "vault."
    )


def quickbooks_status():
    """Non-raising connection report for the dashboard."""
    try:
        creds = quickbooks_credentials()
        return {"provider": "quickbooks", "connected": True,
                "source": creds["source"],
                "detail": "QuickBooks ready (%s)." % creds["source"]}
    except FinanceNotConfigured as e:
        return {"provider": "quickbooks", "connected": False,
                "source": None, "detail": str(e)}


# --------------------------------------------------------------------------- #
# Plaid
# --------------------------------------------------------------------------- #

def plaid_app_credentials():
    """Return ``{client_id, secret, base_url, env, source}`` or raise.

    APP-level credentials only — NO access token. These authenticate our app to
    Plaid for ``link/token/create`` and ``item/public_token/exchange`` (the Plaid
    Link flow). Per-Item access tokens live encrypted in ``plaid_items``.
    Connector proxy first, then vault/env. Fails loud if client_id/secret absent
    so no bank can be linked against missing credentials (no mock tokens).
    """
    env_name = (os.environ.get("PLAID_ENV") or "production").strip().lower()
    base_url = _PLAID_BASE.get(env_name, _PLAID_BASE["production"])

    settings = get_connection_settings("plaid")
    if settings:
        client_id = settings.get("client_id") or settings.get("clientId")
        secret = settings.get("secret") or settings.get("client_secret")
        if client_id and secret:
            return {"client_id": client_id, "secret": secret, "base_url": base_url,
                    "env": env_name, "source": "connector"}

    client_id = _env("PLAID_CLIENT_ID")
    secret = _env("PLAID_SECRET")
    if client_id and secret:
        return {"client_id": client_id, "secret": secret, "base_url": base_url,
                "env": env_name, "source": "vault"}

    raise FinanceNotConfigured(
        "Plaid is not configured. Connect Plaid via Connected Services, or add "
        "PLAID_CLIENT_ID and PLAID_SECRET to the vault. (Plaid client_id and "
        "secret are required before any bank can be linked.)"
    )


def _is_production():
    return os.environ.get("REPLIT_DEPLOYMENT") == "1"


def _plaid_legacy_token():
    """The single legacy access token (connector or PLAID_ACCESS_TOKEN), or None.

    Backward-compatible support for the pre-Link single-Item setup. The preferred
    path is now Plaid Link (per-Item tokens stored in ``plaid_items``)."""
    settings = get_connection_settings("plaid")
    if settings:
        t = settings.get("access_token") or settings.get("accessToken")
        if t:
            return t
    return _env("PLAID_ACCESS_TOKEN")


def _resolve_plaid_items(db):
    """All known Plaid Items with decrypted tokens — stored rows + legacy token.

    Returns plain dicts (never logged). Stored rows whose token fails to decrypt
    (e.g. ENCRYPTION_KEY rotated) are flagged ``decrypt_ok=False`` so callers skip
    them and the UI can prompt a reconnect. The legacy single token is appended as
    a synthetic ``is_legacy`` item, deduped by token against stored rows."""
    from src.models import PlaidItem
    out = []
    seen = set()
    rows = (db.query(PlaidItem)
            .filter(PlaidItem.status != "removed")
            .order_by(PlaidItem.id).all())
    for r in rows:
        token = None
        decrypt_ok = True
        try:
            token = decrypt_value(r.encrypted_access_token)
            seen.add(token)
        except Exception:
            decrypt_ok = False
        out.append({
            "db_id": r.id, "item_id": r.item_id, "access_token": token,
            "institution_name": r.institution_name, "institution_id": r.institution_id,
            "cursor": r.cursor, "status": r.status, "last_error": r.last_error,
            "last_synced_at": r.last_synced_at, "created_at": r.created_at,
            "source": r.source or "link", "is_legacy": False, "decrypt_ok": decrypt_ok,
        })

    legacy = _plaid_legacy_token()
    if legacy and legacy not in seen:
        out.append({
            "db_id": None, "item_id": None, "access_token": legacy,
            "institution_name": None, "institution_id": None, "cursor": None,
            "status": "active", "last_error": None, "last_synced_at": None,
            "created_at": None, "source": "env", "is_legacy": True, "decrypt_ok": True,
        })
    return out


def plaid_items(db):
    """Syncable Plaid Items (those with a usable access token). Used by the sync loop."""
    return [i for i in _resolve_plaid_items(db)
            if i["access_token"] and i["decrypt_ok"] and i["status"] != "removed"]


def list_plaid_items(db):
    """Masked summaries for the UI / API — NEVER includes access tokens."""
    items = []
    for i in _resolve_plaid_items(db):
        ok = i["decrypt_ok"]
        items.append({
            "id": i["db_id"], "item_id": i["item_id"],
            "institution_name": i["institution_name"],
            "institution_id": i["institution_id"],
            "status": i["status"] if ok else "error",
            "last_error": i["last_error"] if ok else (
                "Stored access token is unreadable (encryption key changed). "
                "Disconnect and reconnect this bank."),
            "source": i["source"], "is_legacy": i["is_legacy"],
            "last_synced_at": i["last_synced_at"], "created_at": i["created_at"],
        })
    return items


def save_plaid_item(db, item_id, access_token, institution_name=None,
                    institution_id=None, source="link"):
    """Encrypt + upsert a Plaid Item's access token (keyed on ``item_id``).

    Fails loud in production when ENCRYPTION_KEY is ephemeral — a bank access
    token encrypted with an auto-generated key becomes undecryptable after the
    next restart, which would silently break sync. In dev a warning suffices."""
    from src.models import PlaidItem
    if not item_id or not access_token:
        raise FinanceProviderError("Plaid exchange returned an incomplete item.")
    if not encryption_key_is_persistent():
        if _is_production():
            raise FinanceProviderError(
                "ENCRYPTION_KEY is not set as a persistent secret. Refusing to "
                "store a bank access token that would become undecryptable after "
                "the next restart. Set ENCRYPTION_KEY and reconnect."
            )
        logger.warning(
            "Storing a Plaid access token with an EPHEMERAL ENCRYPTION_KEY — it "
            "will be undecryptable after the next restart. Set ENCRYPTION_KEY as "
            "a persistent secret."
        )
    enc = encrypt_value(access_token)
    row = db.query(PlaidItem).filter(PlaidItem.item_id == item_id).first()
    if row is None:
        row = PlaidItem(item_id=item_id)
        db.add(row)
    row.encrypted_access_token = enc
    if institution_name:
        row.institution_name = institution_name
    if institution_id:
        row.institution_id = institution_id
    row.status = "active"
    row.last_error = None
    row.source = source
    # Re-linking issues a fresh token; the old cursor no longer applies. A full
    # re-sync is safe because transactions upsert on (source, external_id).
    row.cursor = None
    db.commit()
    db.refresh(row)
    return row


def get_plaid_item_token(db, db_id):
    """Return ``(row, decrypted_access_token)`` for a stored Item, or raise."""
    from src.models import PlaidItem
    row = db.query(PlaidItem).filter(PlaidItem.id == db_id).first()
    if row is None:
        raise FinanceNotConfigured("No linked bank with that id.")
    try:
        token = decrypt_value(row.encrypted_access_token)
    except Exception:
        raise FinanceProviderError(
            "The stored access token for this bank is unreadable (encryption key "
            "changed). It can be removed locally but not revoked at Plaid."
        )
    return row, token


def delete_plaid_item(db, db_id):
    """Hard-delete a stored Item row. Returns True if a row was removed."""
    from src.models import PlaidItem
    n = db.query(PlaidItem).filter(PlaidItem.id == db_id).delete()
    db.commit()
    return bool(n)


def plaid_status(db=None):
    """Non-raising connection report for the dashboard.

    Connected once at least one Item is linked (a stored Item or the legacy
    token) AND app credentials are present. Reports masked item summaries +
    count; never raises so the UI can always render an honest state."""
    try:
        app = plaid_app_credentials()
    except FinanceNotConfigured as e:
        return {"provider": "plaid", "connected": False, "source": None,
                "detail": str(e), "items": [], "item_count": 0}

    own = False
    if db is None:
        from src.database import SessionLocal
        db = SessionLocal()
        own = True
    try:
        items = list_plaid_items(db)
    except Exception as e:  # noqa: BLE001 — status must stay non-raising
        logger.warning("Plaid item listing failed: %s", e)
        items = []
    finally:
        if own:
            db.close()

    count = len(items)
    if count == 0:
        return {"provider": "plaid", "connected": False, "source": app["source"],
                "detail": "Plaid app configured (%s). Click “Connect bank” to "
                          "link an account." % app["source"],
                "items": [], "item_count": 0}
    return {"provider": "plaid", "connected": True, "source": app["source"],
            "detail": "%d linked bank account(s)." % count,
            "items": items, "item_count": count}


def provider_status():
    """Combined status for both providers."""
    return {"quickbooks": quickbooks_status(), "plaid": plaid_status()}
