"""Telephony layer (Task #66) — provider-agnostic voice calling.

Gives Fleet bots a phone: outbound + inbound calls whose every turn is governed
(compliance/hallucination scored) and audited, with speech recognised by the
telephony provider and replies spoken via the avatar voice layer (ElevenLabs).

Public seams:
  - ``facade``        — provider-agnostic actions (place_call / validate_webhook / TwiML).
  - ``providers``     — credential sourcing + honest status (fail loud, never mocked).
  - ``conversation``  — the governed per-turn reply loop (writes a DecisionTrace per turn).
  - ``audio_store``   — short-lived, token-gated TTS audio served to the provider.

Twilio is the current backend; adding another provider means adding a client and
extending ``facade._client_for`` only — call sites never import a concrete client.
"""
