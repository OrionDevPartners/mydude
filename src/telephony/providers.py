"""Telephony-provider credential sourcing + honest status (Task #66).

Mirrors ``src/avatar/providers.py``: credentials are decoupled from code and
provider selection (governance pillar #3) — sourced at runtime via the Replit
connector proxy first, then the vault / environment fallback. Provider *names*
are configured per-deployment; secrets are never hardcoded and never logged.

``*_status`` helpers NEVER raise — they report connected/not-connected honestly
so the UI can show a truthful state. The credential accessors DO raise
``TelephonyNotConfigured`` when nothing is configured (fail loud, pillar #1).
"""
import logging
import os

from src.providers.secrets import get_secret
from src.web.connectors import get_connection_settings

logger = logging.getLogger(__name__)


class TelephonyNotConfigured(RuntimeError):
    """No credentials/config for the requested telephony backend."""


class TelephonyAuthError(RuntimeError):
    """The provider rejected our credentials (HTTP 401/403)."""


class TelephonyProviderError(RuntimeError):
    """The provider returned an error or an unrecognized response."""


_TWILIO_BASE = "https://api.twilio.com"
# Accepted vault / env names (provider-agnostic alias set). The canonical names
# are the Twilio defaults; aliases tolerate common variations.
_TWILIO_SID_NAMES = ("TWILIO_ACCOUNT_SID", "TWILIO_SID", "TWILIO_ACCOUNTSID")
_TWILIO_TOKEN_NAMES = ("TWILIO_AUTH_TOKEN", "TWILIO_TOKEN", "TWILIO_AUTHTOKEN")
_TWILIO_FROM_NAMES = (
    "TWILIO_PHONE_NUMBER", "TWILIO_FROM_NUMBER", "TWILIO_CALLER_ID", "TWILIO_FROM",
)


def _val_from_settings(settings, keys):
    """Pull a value out of connector settings without assuming one shape."""
    if not isinstance(settings, dict):
        return None
    for k in keys:
        v = settings.get(k)
        if v:
            return v
    return None


def _first_env(*names):
    for n in names:
        v = get_secret(n)
        if v:
            return v, n
    return None, None


def twilio_credentials():
    """Return Twilio credentials or fail loud.

    ``{account_sid, auth_token, base_url, from_number, source}``. Resolution
    order (pillar #3): connector proxy -> vault/env. Never returns a placeholder;
    raises ``TelephonyNotConfigured`` when the SID or auth token is missing.
    ``from_number`` may be ``None`` (a per-bot number can supply the caller-ID).
    """
    settings = None
    try:
        settings = get_connection_settings("twilio")
    except Exception as e:  # noqa: BLE001 — connector proxy is best-effort
        logger.debug("Twilio connector lookup failed: %s", e)
        settings = None

    sid = _val_from_settings(settings, ("account_sid", "accountSid", "sid", "ACCOUNT_SID"))
    token = _val_from_settings(settings, ("auth_token", "authToken", "token", "AUTH_TOKEN", "api_key", "apiKey"))
    from_number = _val_from_settings(settings, ("phone_number", "phoneNumber", "from", "from_number", "caller_id"))
    source = "connector"

    if not sid:
        sid, _ = _first_env(*_TWILIO_SID_NAMES)
        if sid:
            source = "vault"
    if not token:
        env_token, _ = _first_env(*_TWILIO_TOKEN_NAMES)
        if env_token:
            token = env_token
            source = "vault" if not settings else source
    if not from_number:
        env_from, _ = _first_env(*_TWILIO_FROM_NAMES)
        from_number = env_from

    if not sid or not token:
        raise TelephonyNotConfigured(
            "Twilio is not connected. Add TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN "
            "to the vault (or connect Twilio) to enable phone calls."
        )

    return {
        "account_sid": sid,
        "auth_token": token,
        "base_url": _TWILIO_BASE,
        "from_number": from_number,
        "source": source,
    }


def telephony_status():
    """Honest, non-raising status for the telephony backend."""
    base = {"provider": "twilio"}
    try:
        creds = twilio_credentials()
    except TelephonyNotConfigured as e:
        return {**base, "connected": False, "source": None,
                "from_number": None, "detail": str(e)}
    return {
        **base,
        "connected": True,
        "source": creds["source"],
        "from_number": creds.get("from_number"),
        "detail": "Twilio telephony ready (via %s)." % creds["source"],
    }


def telephony_configured():
    return telephony_status()["connected"]


def public_base_url():
    """Return the public ``https://host`` the provider can reach us on. Fail loud.

    Telephony providers fetch TwiML and media over the public internet and post
    webhooks back, so we need an absolute, externally-resolvable base URL.
    Resolution order:
      1. ``PUBLIC_BASE_URL`` operator override (any environment).
      2. In dev (REPLIT_DEPLOYMENT != "1"): ``REPLIT_DEV_DOMAIN``.
      3. ``REPLIT_DOMAINS`` (production, comma-separated → first).
      4. ``REPLIT_DEV_DOMAIN`` as a last resort.
    Raises ``TelephonyNotConfigured`` when none is available rather than emitting
    a localhost/placeholder URL the provider could never reach (pillar #1).
    """
    override = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")

    is_prod = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    dev = (os.environ.get("REPLIT_DEV_DOMAIN") or "").strip()
    if not is_prod and dev:
        return "https://" + dev

    domains = (os.environ.get("REPLIT_DOMAINS") or "").strip()
    if domains:
        first = domains.split(",")[0].strip()
        if first:
            return "https://" + first

    if dev:
        return "https://" + dev

    raise TelephonyNotConfigured(
        "No public base URL is available. Set PUBLIC_BASE_URL to the externally "
        "reachable https origin so the telephony provider can fetch audio and "
        "post call webhooks."
    )
