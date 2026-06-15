"""env_2 access layer — the single place the app reads secrets/settings from
the environment (Replit Secrets / the credential vault sync target).

Secrets values are NEVER hardcoded and NEVER committed; they only ever live in
the process environment, populated by Replit Secrets or by the credential vault
sync at boot. Code asks for a secret by *name* (declared in env_1).

Secret resolution precedence (Governance Pillar #3 — separate provider from
secrets, sourced at runtime via the connector proxy first):
  1. Replit connector proxy (REPLIT_CONNECTORS_HOSTNAME + identity token)
     — resolves OAuth tokens for connected integrations without the operator
     manually copying keys.
  2. Process environment / Replit Secrets (env_2 fallback)
     — covers all explicitly-set secrets and vault-synced keys.
"""
import os
from typing import Optional


class MissingSecretError(RuntimeError):
    """Raised when a required secret name has no value in the environment."""


def _try_connector_proxy(name: str) -> Optional[str]:
    """Attempt to resolve ``name`` via the Replit connector proxy (env_1 tier 1).

    Returns None when the proxy is unavailable or the secret is not found —
    caller falls through to the env_2 fallback. Never raises; a proxy failure
    must not break secret resolution for env-backed secrets.
    """
    try:
        from src.web.connectors import get_connection_settings, proxy_available
        if not proxy_available():
            return None
        settings = get_connection_settings(name)
        if settings and isinstance(settings, dict):
            for v in settings.values():
                if v:
                    return str(v)
    except Exception:
        pass
    return None


def get_secret(name: str) -> Optional[str]:
    """Return the secret value for ``name`` or None if unset/empty.

    Resolution order:
      1. Connector proxy (OAuth tokens for connected integrations)
      2. Process environment / Replit Secrets
    """
    if not name:
        return None
    # Tier 1: connector proxy
    proxy_val = _try_connector_proxy(name)
    if proxy_val:
        return proxy_val
    # Tier 2: process environment (Replit Secrets / vault sync target)
    val = os.environ.get(name)
    return val or None


def has_secret(name: str) -> bool:
    """True if ``name`` resolves to a non-empty value via any tier."""
    return bool(get_secret(name))


def require_secret(name: str) -> str:
    """Return the secret value or raise MissingSecretError."""
    val = get_secret(name)
    if not val:
        raise MissingSecretError(name)
    return val


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a non-secret tuning/setting env var (model name, concurrency, ...).

    Settings differ from secrets: they are safe to default and are not subject
    to the boot handshake. Still funneled through this module so all environment
    reads are centralized.
    """
    if not name:
        return default
    val = os.environ.get(name)
    return val if (val is not None and val != "") else default
