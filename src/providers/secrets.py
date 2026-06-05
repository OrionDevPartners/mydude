"""env_2 access layer — the single place the app reads secrets/settings from
the environment (Replit Secrets / the credential vault sync target).

Secrets values are NEVER hardcoded and NEVER committed; they only ever live in
the process environment, populated by Replit Secrets or by the credential vault
sync at boot. Code asks for a secret by *name* (declared in env_1).
"""
import os
from typing import Optional


class MissingSecretError(RuntimeError):
    """Raised when a required secret name has no value in the environment."""


def get_secret(name: str) -> Optional[str]:
    """Return the secret value for ``name`` or None if unset/empty."""
    if not name:
        return None
    val = os.environ.get(name)
    return val or None


def has_secret(name: str) -> bool:
    """True if ``name`` resolves to a non-empty value in the environment."""
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
