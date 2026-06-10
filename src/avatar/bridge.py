"""Provider-agnostic bridge to the EXTERNAL GPU avatar stack.

Realistic real-time avatar VIDEO requires GPU + WebRTC infrastructure the Replit
container cannot host. This module ONLY negotiates a streaming session over HTTPS
against an external provider and returns the WebRTC/LiveKit connection info the
**browser** uses to connect DIRECTLY to that provider. It never hosts GPU rendering
and never relays media (architectural boundary in task #72).

Supported backends (selected per-profile, governance pillar #2):
  * ``heygen``  — HeyGen Streaming API (``streaming.new``), keyed by HEYGEN_API_KEY
                  (vault) or the ``heygen`` connector.
  * ``azure`` / ``nvidia-ace`` / ``custom`` — a GPU avatar service the operator
                  deploys on the Azure stack, reached via the configured
                  ``AVATAR_BRIDGE_URL`` (+ optional ``AVATAR_BRIDGE_TOKEN``).

``avatar_status`` NEVER raises (honest UI state). ``create_session`` fails loud
(``AvatarNotConfigured`` / ``AvatarProviderError``) and NEVER fabricates connection
info — the session layer degrades to voice-only when avatar is unconfigured.
"""
import logging

import httpx

from src.providers.secrets import get_secret, get_env
from src.web.connectors import get_connection_settings
from src.avatar.providers import (
    _key_from_settings,
    AvatarNotConfigured,
    AvatarAuthError,
    AvatarProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_HEYGEN_BASE = "https://api.heygen.com"
_HEYGEN_ENV_NAMES = ("HEYGEN_API_KEY", "HEYGEN_KEY")

# Provider slugs that route to a configured external (Azure-hosted) bridge endpoint.
_BRIDGE_SLUGS = ("azure", "nvidia-ace", "ace", "custom", "audio2face")


def _heygen_key():
    try:
        settings = get_connection_settings("heygen")
    except Exception as e:  # noqa: BLE001
        logger.debug("HeyGen connector lookup failed: %s", e)
        settings = None
    key = _key_from_settings(settings)
    if key:
        return key, "connector"
    for n in _HEYGEN_ENV_NAMES:
        v = get_secret(n)
        if v:
            return v, "vault"
    return None, None


def _bridge_config():
    """Return (url, token) for the externally-deployed avatar bridge, if configured."""
    url = get_env("AVATAR_BRIDGE_URL")
    token = get_secret("AVATAR_BRIDGE_TOKEN")
    return (url or None), (token or None)


def _is_https(url):
    """The bridge carries a bearer token — we refuse anything but HTTPS so the
    AVATAR_BRIDGE_TOKEN is never sent in plaintext (governance pillar #3)."""
    return bool(url) and url.strip().lower().startswith("https://")


def avatar_status():
    """Honest, non-raising status for each external avatar backend."""
    heygen_key, heygen_src = _heygen_key()
    bridge_url, _bridge_token = _bridge_config()
    bridge_ok = _is_https(bridge_url)
    if bridge_ok:
        azure_detail = "External avatar bridge configured (%s)." % bridge_url
    elif bridge_url:
        azure_detail = ("AVATAR_BRIDGE_URL must use https:// — refusing to send the "
                        "bridge token over plaintext. Update it to an https URL.")
    else:
        azure_detail = ("Not configured. Deploy the GPU avatar service on the Azure "
                        "stack and set AVATAR_BRIDGE_URL.")
    providers = {
        "heygen": {
            "configured": bool(heygen_key),
            "source": heygen_src,
            "detail": ("HeyGen Streaming ready (via %s)." % heygen_src) if heygen_key
            else "Not connected. Add HEYGEN_API_KEY in the vault to enable HeyGen "
                 "avatars.",
        },
        "azure": {
            "configured": bridge_ok,
            "source": "env" if bridge_ok else None,
            "detail": azure_detail,
        },
    }
    configured = bool(heygen_key) or bridge_ok
    return {
        "configured": configured,
        "providers": providers,
        "detail": ("At least one avatar backend is configured." if configured else
                   "No avatar backend configured — sessions fall back to voice-only. "
                   "Realistic video runs on the external Azure/GPU stack."),
    }


def avatar_configured(provider=None):
    """True if the given (or any) avatar backend is configured.

    A bridge URL only counts as configured when it is HTTPS — a plaintext URL is
    treated as unconfigured so we never negotiate (and leak the token) over it."""
    heygen_key, _ = _heygen_key()
    bridge_url, _ = _bridge_config()
    bridge_ok = _is_https(bridge_url)
    if provider is None:
        return bool(heygen_key) or bridge_ok
    p = (provider or "").lower()
    if p == "heygen":
        return bool(heygen_key)
    if p in _BRIDGE_SLUGS:
        return bridge_ok
    return False


def _heygen_new_session(avatar_config, voice_id=None):
    """Negotiate a HeyGen streaming session over HTTPS. Returns connection info.

    Returns the LiveKit/WebRTC connection descriptor the browser connects with —
    we do not proxy media. Fails loud; never fabricates.
    """
    key, source = _heygen_key()
    if not key:
        raise AvatarNotConfigured("HeyGen is not configured.")
    cfg = avatar_config if isinstance(avatar_config, dict) else {}
    body = {
        "quality": cfg.get("quality", "high"),
        "version": cfg.get("version", "v2"),
    }
    if cfg.get("avatar_id"):
        body["avatar_id"] = cfg["avatar_id"]
    if cfg.get("avatar_name"):
        body["avatar_name"] = cfg["avatar_name"]
    voice_block = {}
    if voice_id:
        voice_block["voice_id"] = voice_id
    if cfg.get("voice_id"):
        voice_block["voice_id"] = cfg["voice_id"]
    if voice_block:
        body["voice"] = voice_block
    url = "%s/v1/streaming.new" % _HEYGEN_BASE
    try:
        resp = httpx.post(url, json=body,
                          headers={"x-api-key": key, "Content-Type": "application/json",
                                   "Accept": "application/json"},
                          timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise AvatarProviderError("HeyGen streaming.new request failed: %s" % e)
    if resp.status_code in (401, 403):
        raise AvatarAuthError(
            "HeyGen rejected the request (HTTP %d). Check HEYGEN_API_KEY in the vault."
            % resp.status_code)
    if resp.status_code >= 400:
        raise AvatarProviderError(
            "HeyGen streaming.new error (HTTP %d): %s"
            % (resp.status_code, resp.text[:300]))
    try:
        data = resp.json()
    except ValueError as e:
        raise AvatarProviderError("HeyGen returned non-JSON: %s" % e)
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or not (
            payload.get("session_id") or payload.get("url") or payload.get("access_token")):
        raise AvatarProviderError(
            "HeyGen streaming.new returned no usable connection info: %s"
            % str(data)[:200])
    return {
        "provider": "heygen",
        "source": source,
        "transport": "livekit",
        "connection": payload,
        "detail": "HeyGen streaming session negotiated; the browser connects "
                  "directly via LiveKit.",
    }


def _bridge_new_session(provider, persona=None, avatar_config=None, voice_id=None):
    """Negotiate a session against the externally-deployed (Azure) avatar bridge."""
    url, token = _bridge_config()
    if not url:
        raise AvatarNotConfigured(
            "No external avatar bridge configured. Set AVATAR_BRIDGE_URL to the GPU "
            "avatar service on the Azure stack.")
    if not _is_https(url):
        # Never send the bridge bearer token over plaintext (governance pillar #3).
        raise AvatarNotConfigured(
            "AVATAR_BRIDGE_URL must use https:// — refusing to negotiate (and send "
            "the bridge token) over plaintext.")
    body = {
        "provider": provider,
        "persona": persona,
        "avatar_config": avatar_config or {},
        "voice_id": voice_id,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    try:
        resp = httpx.post(url.rstrip("/"), json=body, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise AvatarProviderError("Avatar bridge request failed: %s" % e)
    if resp.status_code in (401, 403):
        raise AvatarAuthError(
            "The avatar bridge rejected the request (HTTP %d). Check "
            "AVATAR_BRIDGE_TOKEN." % resp.status_code)
    if resp.status_code >= 400:
        raise AvatarProviderError(
            "Avatar bridge error (HTTP %d): %s" % (resp.status_code, resp.text[:300]))
    try:
        data = resp.json()
    except ValueError as e:
        raise AvatarProviderError("Avatar bridge returned non-JSON: %s" % e)
    connection = data.get("connection") if isinstance(data, dict) else None
    if not connection:
        raise AvatarProviderError(
            "Avatar bridge returned no connection info: %s" % str(data)[:200])
    return {
        "provider": provider,
        "source": "env",
        "transport": data.get("transport", "webrtc"),
        "connection": connection,
        "detail": "External avatar bridge session negotiated; the browser connects "
                  "directly to the GPU stack.",
    }


def create_session(provider, persona=None, avatar_config=None, voice_id=None):
    """Negotiate a real avatar streaming session against the configured backend.

    Returns a connection descriptor for the browser to connect DIRECTLY to the
    provider's WebRTC endpoint. Raises ``AvatarNotConfigured`` when the requested
    backend is not configured and ``AvatarProviderError``/``AvatarAuthError`` on a
    failed negotiation. Never returns fabricated connection info.
    """
    p = (provider or "").lower().strip()
    if not p:
        raise AvatarNotConfigured("No avatar provider selected on the profile.")
    if p == "heygen":
        return _heygen_new_session(avatar_config, voice_id=voice_id)
    if p in _BRIDGE_SLUGS:
        return _bridge_new_session(p, persona=persona, avatar_config=avatar_config,
                                   voice_id=voice_id)
    raise AvatarNotConfigured("Unknown avatar provider '%s'." % provider)
