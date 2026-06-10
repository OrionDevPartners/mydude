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

def plaid_credentials():
    """Return ``{client_id, secret, access_token, base_url, source}`` or raise.

    Note: the env fallback supports exactly one Plaid Item per PLAID_ACCESS_TOKEN
    (MVP limit). The connector path can carry the same single-item token today.
    """
    env_name = (os.environ.get("PLAID_ENV") or "production").strip().lower()
    base_url = _PLAID_BASE.get(env_name, _PLAID_BASE["production"])

    settings = get_connection_settings("plaid")
    if settings:
        client_id = settings.get("client_id") or settings.get("clientId")
        secret = settings.get("secret") or settings.get("client_secret")
        access_token = (
            settings.get("access_token")
            or settings.get("accessToken")
            or _env("PLAID_ACCESS_TOKEN")
        )
        if client_id and secret and access_token:
            return {"client_id": client_id, "secret": secret,
                    "access_token": access_token, "base_url": base_url,
                    "source": "connector"}

    client_id = _env("PLAID_CLIENT_ID")
    secret = _env("PLAID_SECRET")
    access_token = _env("PLAID_ACCESS_TOKEN")
    if client_id and secret and access_token:
        return {"client_id": client_id, "secret": secret,
                "access_token": access_token, "base_url": base_url,
                "source": "vault"}

    raise FinanceNotConfigured(
        "Plaid is not connected. Connect Plaid via Connected Services, or add "
        "PLAID_CLIENT_ID, PLAID_SECRET and PLAID_ACCESS_TOKEN to the vault."
    )


def plaid_status():
    """Non-raising connection report for the dashboard."""
    try:
        creds = plaid_credentials()
        return {"provider": "plaid", "connected": True,
                "source": creds["source"],
                "detail": "Plaid ready (%s)." % creds["source"]}
    except FinanceNotConfigured as e:
        return {"provider": "plaid", "connected": False,
                "source": None, "detail": str(e)}


def provider_status():
    """Combined status for both providers."""
    return {"quickbooks": quickbooks_status(), "plaid": plaid_status()}
