"""Provider-agnostic voice facade.

Call sites use ``synthesize`` / ``list_voices`` / ``voice_status`` here and never
import a concrete provider client — the provider is selected behind this seam
(governance pillar #2). ElevenLabs is the current backend; adding another voice
provider means adding a client and extending ``_client_for`` only.
"""
import logging

from src.avatar.providers import (
    voice_status,
    voice_configured,
    AvatarNotConfigured,
)

logger = logging.getLogger(__name__)

# Re-exported so router/tests can depend on the facade, not the provider module.
__all__ = [
    "voice_status", "voice_configured", "list_voices", "synthesize",
    "AvatarNotConfigured",
]


def _client_for(provider=None):
    """Return a voice client for ``provider`` (defaults to the configured one)."""
    name = (provider or "elevenlabs").lower()
    if name == "elevenlabs":
        from src.avatar.client_elevenlabs import ElevenLabsClient
        return ElevenLabsClient()
    raise AvatarNotConfigured("Unknown voice provider '%s'." % provider)


def list_voices(provider=None):
    """List the available voices for the configured voice provider. Fail loud."""
    return _client_for(provider).list_voices()


def synthesize(text, voice_id, provider=None, model_id=None):
    """Synthesize speech -> (audio_bytes, content_type). Fail loud."""
    return _client_for(provider).synthesize(text, voice_id, model_id=model_id)
