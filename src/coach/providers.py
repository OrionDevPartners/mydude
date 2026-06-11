"""Credential sourcing + connection status for the mood/emotion provider(s).

Provider-agnostic (governance pillar #2): the coach captures emotion through a
provider object exposing ``analyze_text(text) -> dict`` and (optionally)
``analyze_audio(audio_bytes, filename) -> dict`` for voice/prosody. Hume AI is
the current implementation; selecting another provider is a config + client
change, never a call-site change.

Sourcing order (mirrors finance providers):
  1. Replit connector proxy (fetched fresh every call, never cached)
  2. Vault / environment variable (``HUME_API_KEY``)
  3. Neither configured -> ``CoachNotConfigured`` (fail loud, no mock emotion data)

Status functions never raise — they report honestly so the UI can tell the
operator exactly what to connect. Credential functions raise when unconfigured.

NOTE: Hume's Expression Measurement API is being sunset (last usable day
``2026-06-14``). Status surfaces this so the operator can plan a swap — the
provider-agnostic interface is what makes that swap a localized change.
"""
import os
import logging

from src.web.connectors import get_connection_settings

logger = logging.getLogger(__name__)


class CoachNotConfigured(RuntimeError):
    """Raised when a required coach provider has no usable credentials."""


class CoachAuthError(RuntimeError):
    """Raised when a provider rejects our credentials (expired / invalid)."""


class CoachProviderError(RuntimeError):
    """Raised on a non-auth provider/API failure."""


_HUME_BASE = "https://api.hume.ai"
_HUME_SUNSET = "2026-06-14"


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return None


# --------------------------------------------------------------------------- #
# Hume AI (Expression Measurement — language emotion)
# --------------------------------------------------------------------------- #

def hume_credentials():
    """Return ``{api_key, base_url, source}`` or raise ``CoachNotConfigured``."""
    settings = get_connection_settings("hume")
    if settings:
        key = (
            settings.get("api_key")
            or settings.get("apiKey")
            or settings.get("access_token")
        )
        if key:
            return {"api_key": key, "base_url": _HUME_BASE, "source": "connector"}

    key = _env("HUME_API_KEY", "HUME_AI_API_KEY")
    if key:
        return {"api_key": key, "base_url": _HUME_BASE, "source": "vault"}

    raise CoachNotConfigured(
        "Hume is not connected. Add HUME_API_KEY to the vault to enable mood "
        "capture. (Note: Hume's Expression Measurement API sunsets %s.)"
        % _HUME_SUNSET
    )


def hume_status():
    """Non-raising connection report for the dashboard."""
    base = {"provider": "hume", "sunset": _HUME_SUNSET}
    try:
        creds = hume_credentials()
        return {
            **base,
            "connected": True,
            "source": creds["source"],
            "detail": "Hume ready (%s). Expression Measurement API sunsets %s."
            % (creds["source"], _HUME_SUNSET),
        }
    except CoachNotConfigured as e:
        return {**base, "connected": False, "source": None, "detail": str(e)}


# --------------------------------------------------------------------------- #
# Provider-agnostic selection
# --------------------------------------------------------------------------- #

def active_mood_provider_name():
    """The configured emotion provider slug (default 'hume')."""
    return (os.environ.get("MOOD_PROVIDER") or "hume").strip().lower()


def get_mood_provider():
    """Return a configured mood provider exposing ``analyze_text(text)``.

    Provider-agnostic dispatch point. Raises ``CoachNotConfigured`` when the
    selected provider has no usable credentials (the client constructor sources
    and validates them).
    """
    name = active_mood_provider_name()
    if name == "hume":
        from src.coach.client_hume import HumeClient
        return HumeClient()
    raise CoachNotConfigured(
        "Unknown mood provider '%s'. Set MOOD_PROVIDER to a supported provider "
        "(currently: hume)." % name
    )


def mood_provider_status():
    """Combined status for the mood provider layer."""
    name = active_mood_provider_name()
    if name == "hume":
        return {"active": name, "hume": hume_status()}
    return {
        "active": name,
        name: {
            "provider": name, "connected": False, "source": None,
            "detail": "Unknown provider '%s'. Supported: hume." % name,
        },
    }
