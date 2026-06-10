"""Humanistic avatar layer (Azure-tier).

Gives a bot a presentation identity — persona + voice + avatar — and runs live,
disclosure/consent-gated sessions:

  * ``providers``  — credential sourcing (connector proxy -> vault) + honest,
                     non-raising status for the ElevenLabs voice backend.
  * ``client_elevenlabs`` — real ElevenLabs TTS httpx client (fail loud).
  * ``voice``      — provider-agnostic voice facade (synthesize / list_voices).
  * ``bridge``     — provider-agnostic bridge to the EXTERNAL GPU avatar stack
                     (HeyGen Streaming / Azure-hosted NVIDIA ACE). NEGOTIATES the
                     session over HTTPS and returns WebRTC connection info for the
                     browser; never hosts GPU rendering or relays media.
  * ``compliance`` — AI-use disclosure text + recording-consent enforcement.
  * ``profiles``   — AvatarProfile CRUD.
  * ``sessions``   — two-phase, consent-gated session lifecycle with a voice-only
                     fallback.

Every module honors the MyDude.io governance pillars: no placeholders / fail loud,
provider-agnostic code, provider separated from secrets, and a full audit trail.
"""
