"""Provider-agnostic telephony facade (Task #66).

Call sites use these functions and never import a concrete provider client — the
provider is selected behind this seam (governance pillar #2). Twilio is the
current backend; adding another means adding a client and extending
``_client_for`` only.

TwiML builders are re-exported here so routes depend on the facade, not the
Twilio module directly. (TwiML is a Twilio markup detail today; if a second
provider with a different call-control format is added, these become a dispatch.)
"""
import logging

from src.telephony.providers import (
    telephony_status,
    telephony_configured,
    public_base_url,
    TelephonyNotConfigured,
    TelephonyAuthError,
    TelephonyProviderError,
)
from src.telephony.client_twilio import (
    twiml_gather,
    twiml_play_hangup,
    twiml_say,
)

logger = logging.getLogger(__name__)

__all__ = [
    "telephony_status", "telephony_configured", "public_base_url",
    "place_call", "fetch_call", "validate_webhook",
    "twiml_gather", "twiml_play_hangup", "twiml_say",
    "TelephonyNotConfigured", "TelephonyAuthError", "TelephonyProviderError",
]


def _client_for(provider=None):
    """Return a telephony client for ``provider`` (defaults to the configured one)."""
    name = (provider or "twilio").lower()
    if name == "twilio":
        from src.telephony.client_twilio import TwilioClient
        return TwilioClient()
    raise TelephonyNotConfigured("Unknown telephony provider '%s'." % provider)


def place_call(to_number, answer_url, from_number=None,
               status_callback=None, provider=None):
    """Place an outbound call. Returns ``{sid, status, to, from}``. Fail loud.

    ``from_number`` defaults to the provider's configured caller-ID; if neither a
    per-bot number nor a provider default is set we fail loud rather than guess.
    """
    client = _client_for(provider)
    frm = (from_number or client.from_number or "").strip() or None
    if not frm:
        raise TelephonyNotConfigured(
            "No caller-ID is configured. Set the bot's phone_number or a provider "
            "default (e.g. TWILIO_PHONE_NUMBER) before placing calls."
        )
    return client.place_call(to_number, frm, answer_url, status_callback=status_callback)


def fetch_call(sid, provider=None):
    """Read a call's current status from the provider. Fail loud."""
    return _client_for(provider).fetch_call(sid)


def validate_webhook(url, params, signature, provider=None):
    """Verify an inbound webhook signature. Returns bool.

    Constructing the client sources credentials (fail loud if unconfigured), so
    an unconfigured deployment rejects every webhook — there is no unsigned path.
    """
    return _client_for(provider).validate_signature(url, params, signature)
