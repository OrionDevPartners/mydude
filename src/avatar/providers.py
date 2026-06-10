"""Voice-provider credential sourcing + honest status for the avatar layer.

Credentials are decoupled from code and provider selection (governance pillar #3):
they are sourced at runtime via the Replit connector proxy first, then the vault /
environment fallback. Provider *names* are configured per-profile; secrets are never
hardcoded and never logged.

``*_status`` helpers NEVER raise — they report connected/not-connected honestly so
the UI can show a truthful state. The credential accessors DO raise
``AvatarNotConfigured`` when nothing is configured (fail loud, pillar #1).
"""
import logging

from src.providers.secrets import get_secret
from src.web.connectors import get_connection_settings

logger = logging.getLogger(__name__)


class AvatarNotConfigured(RuntimeError):
    """No credentials/config for the requested voice or avatar backend."""


class AvatarAuthError(RuntimeError):
    """The provider rejected our credentials (HTTP 401/403)."""


class AvatarProviderError(RuntimeError):
    """The provider returned an error or an unrecognized response."""


_ELEVEN_BASE = "https://api.elevenlabs.io"
# Accepted vault / env names for the ElevenLabs key (provider-agnostic alias set).
_ELEVEN_ENV_NAMES = ("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "ELEVEN_LABS_API_KEY")


def _key_from_settings(settings):
    """Pull an API key out of connector settings without assuming one shape."""
    if not isinstance(settings, dict):
        return None
    for k in ("api_key", "apiKey", "API_KEY", "key", "access_token", "accessToken"):
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


def elevenlabs_credentials():
    """Return ElevenLabs credentials {api_key, base_url, source} or fail loud.

    Resolution order (pillar #3): connector proxy -> vault/env. Never returns a
    placeholder; raises ``AvatarNotConfigured`` when nothing is configured.
    """
    try:
        settings = get_connection_settings("elevenlabs")
    except Exception as e:  # noqa: BLE001 — connector proxy is best-effort
        logger.debug("ElevenLabs connector lookup failed: %s", e)
        settings = None
    key = _key_from_settings(settings)
    if key:
        return {"api_key": key, "base_url": _ELEVEN_BASE, "source": "connector"}

    key, _name = _first_env(*_ELEVEN_ENV_NAMES)
    if key:
        return {"api_key": key, "base_url": _ELEVEN_BASE, "source": "vault"}

    raise AvatarNotConfigured(
        "ElevenLabs is not connected. Add an ELEVENLABS_API_KEY in the vault "
        "(or connect ElevenLabs) to enable voice synthesis."
    )


def voice_status():
    """Honest, non-raising status for the voice backend."""
    base = {"provider": "elevenlabs"}
    try:
        creds = elevenlabs_credentials()
    except AvatarNotConfigured as e:
        return {**base, "connected": False, "source": None, "detail": str(e)}
    return {
        **base,
        "connected": True,
        "source": creds["source"],
        "detail": "ElevenLabs voice ready (via %s)." % creds["source"],
    }


def voice_configured():
    return voice_status()["connected"]
