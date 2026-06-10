"""ElevenLabs Text-to-Speech client (httpx).

Real outbound voice synthesis. Fails loud on auth errors, provider errors, network
failures, or empty audio — it never fabricates or returns silent placeholder audio
(governance pillar #1). ``list_voices`` doubles as a credential check.

Endpoint shape (verified June 2026):
  GET  {base}/v1/voices                     -> {"voices": [{voice_id, name, ...}]}
  POST {base}/v1/text-to-speech/{voice_id}  body {"text", "model_id", ...}
                                            -> audio/mpeg bytes
Auth: header ``xi-api-key``.
"""
import logging

import httpx

from src.avatar.providers import (
    elevenlabs_credentials,
    AvatarAuthError,
    AvatarProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_MAX_TEXT = 5000
_DEFAULT_MODEL = "eleven_multilingual_v2"


class ElevenLabsClient:
    provider = "elevenlabs"

    def __init__(self):
        creds = elevenlabs_credentials()
        self._key = creds["api_key"]
        self._base = creds["base_url"].rstrip("/")
        self.source = creds["source"]

    def _headers(self, json_ct=False, accept="application/json"):
        h = {"xi-api-key": self._key, "Accept": accept}
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def _raise_for_status(self, resp, what):
        if resp.status_code in (401, 403):
            raise AvatarAuthError(
                "ElevenLabs rejected the request on %s (HTTP %d). Check the "
                "ELEVENLABS_API_KEY in the vault." % (what, resp.status_code)
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.text[:300]
            except Exception:  # noqa: BLE001
                pass
            raise AvatarProviderError(
                "ElevenLabs API error on %s (HTTP %d): %s"
                % (what, resp.status_code, detail)
            )

    def list_voices(self):
        """Return [{voice_id, name, category, preview_url}]. Also validates the key."""
        url = "%s/v1/voices" % self._base
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise AvatarProviderError("ElevenLabs voices request failed: %s" % e)
        self._raise_for_status(resp, "list voices")
        try:
            data = resp.json()
        except ValueError as e:
            raise AvatarProviderError("ElevenLabs returned non-JSON voices: %s" % e)
        out = []
        for v in (data.get("voices") or []):
            vid = v.get("voice_id")
            if not vid:
                continue
            out.append({
                "voice_id": vid,
                "name": v.get("name"),
                "category": v.get("category"),
                "preview_url": v.get("preview_url"),
            })
        return out

    def synthesize(self, text, voice_id, model_id=None):
        """Synthesize ``text`` with ``voice_id`` -> (audio_bytes, content_type)."""
        if not text or not text.strip():
            raise AvatarProviderError("Cannot synthesize empty text.")
        if not voice_id or not str(voice_id).strip():
            raise AvatarProviderError("A voice_id is required to synthesize.")
        clean = text.strip()[:_MAX_TEXT]
        url = "%s/v1/text-to-speech/%s" % (self._base, str(voice_id).strip())
        body = {"text": clean, "model_id": model_id or _DEFAULT_MODEL}
        try:
            resp = httpx.post(url, json=body,
                              headers=self._headers(True, accept="audio/mpeg"),
                              timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise AvatarProviderError("ElevenLabs synthesis request failed: %s" % e)
        self._raise_for_status(resp, "synthesize")
        if not resp.content:
            raise AvatarProviderError("ElevenLabs returned empty audio.")
        content_type = resp.headers.get("Content-Type") or "audio/mpeg"
        return resp.content, content_type
