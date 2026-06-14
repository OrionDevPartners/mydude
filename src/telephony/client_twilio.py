"""Twilio voice client (httpx + stdlib). Task #66.

No vendor SDK — this mirrors the codebase convention (avatar ElevenLabs and
sales Calendly both call the provider HTTP API directly via httpx). That keeps
the dependency closure small (avoids the documented uv-lock fragility) and keeps
the provider behind a swappable seam (pillar #2).

Provides:
  - ``place_call``        — outbound call via the REST Calls API.
  - ``validate_signature``— X-Twilio-Signature HMAC-SHA1 verification (stdlib).
  - ``fetch_call``        — read a call's status from the REST API.

Real outbound action — fails loud on auth/provider/network errors; never
fabricates a call SID or a "queued" status it didn't get from Twilio (pillar #1).

Auth: HTTP Basic (account_sid:auth_token).
Signature algorithm (verified June 2026): HMAC-SHA1 keyed by the auth token over
``full_url + "".join(key + value for key,value in sorted(POST_params))``, then
base64-encoded and constant-time compared to the ``X-Twilio-Signature`` header.
TwiML is built as plain XML strings (see the module-level builders below).
"""
import base64
import hashlib
import hmac
import logging
from xml.sax.saxutils import escape as _xml_escape, quoteattr as _xml_attr

import httpx

from src.telephony.providers import (
    twilio_credentials,
    TelephonyAuthError,
    TelephonyProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
# Twilio call lifecycle events we ask to be notified about on the status callback.
_STATUS_EVENTS = ["initiated", "ringing", "answered", "completed"]


class TwilioClient:
    provider = "twilio"

    def __init__(self):
        creds = twilio_credentials()
        self._sid = creds["account_sid"]
        self._token = creds["auth_token"]
        self._base = creds["base_url"].rstrip("/")
        self.from_number = creds.get("from_number")
        self.source = creds["source"]

    # -- helpers --------------------------------------------------------------
    def _auth(self):
        return (self._sid, self._token)

    def _raise_for_status(self, resp, what):
        if resp.status_code in (401, 403):
            raise TelephonyAuthError(
                "Twilio rejected the request on %s (HTTP %d). Check "
                "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in the vault."
                % (what, resp.status_code)
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.text[:300]
            except Exception:  # noqa: BLE001
                pass
            raise TelephonyProviderError(
                "Twilio API error on %s (HTTP %d): %s" % (what, resp.status_code, detail)
            )

    # -- actions --------------------------------------------------------------
    def place_call(self, to_number, from_number, answer_url, status_callback=None):
        """Start an outbound call. Returns ``{sid, status, to, from}``. Fail loud."""
        to_number = (to_number or "").strip()
        from_number = (from_number or "").strip()
        answer_url = (answer_url or "").strip()
        if not to_number:
            raise TelephonyProviderError("A destination number is required to place a call.")
        if not from_number:
            raise TelephonyProviderError("A caller-ID (from) number is required to place a call.")
        if not answer_url:
            raise TelephonyProviderError("An answer URL is required to place a call.")

        url = "%s/2010-04-01/Accounts/%s/Calls.json" % (self._base, self._sid)
        data = {"To": to_number, "From": from_number, "Url": answer_url, "Method": "POST"}
        if status_callback:
            data["StatusCallback"] = status_callback
            data["StatusCallbackMethod"] = "POST"
            data["StatusCallbackEvent"] = _STATUS_EVENTS  # httpx encodes lists as repeated keys
        try:
            resp = httpx.post(url, data=data, auth=self._auth(), timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise TelephonyProviderError("Twilio call request failed: %s" % e)
        self._raise_for_status(resp, "place call")
        try:
            body = resp.json()
        except ValueError as e:
            raise TelephonyProviderError("Twilio returned non-JSON for place call: %s" % e)
        sid = body.get("sid")
        if not sid:
            raise TelephonyProviderError("Twilio did not return a call SID.")
        return {
            "sid": sid,
            "status": body.get("status"),
            "to": body.get("to") or to_number,
            "from": body.get("from") or from_number,
        }

    def fetch_call(self, sid):
        """Read a call's current status from Twilio. Returns ``{sid, status, ...}``."""
        sid = (sid or "").strip()
        if not sid:
            raise TelephonyProviderError("A call SID is required.")
        url = "%s/2010-04-01/Accounts/%s/Calls/%s.json" % (self._base, self._sid, sid)
        try:
            resp = httpx.get(url, auth=self._auth(), timeout=_TIMEOUT)
        except httpx.HTTPError as e:
            raise TelephonyProviderError("Twilio call fetch failed: %s" % e)
        self._raise_for_status(resp, "fetch call")
        try:
            body = resp.json()
        except ValueError as e:
            raise TelephonyProviderError("Twilio returned non-JSON for fetch call: %s" % e)
        return {
            "sid": body.get("sid") or sid,
            "status": body.get("status"),
            "duration": body.get("duration"),
            "to": body.get("to"),
            "from": body.get("from"),
        }

    def validate_signature(self, url, params, signature):
        """Verify an ``X-Twilio-Signature`` for a webhook. Returns bool. Never raises."""
        return validate_signature(self._token, url, params, signature)


# -- signature verification (stdlib, no client/creds object required) ---------
def validate_signature(auth_token, url, params, signature):
    """Constant-time X-Twilio-Signature check. ``params`` are the POST form fields.

    For GET / no-body requests pass an empty ``params`` dict; the signature is
    then computed over ``url`` alone. Returns ``False`` on any missing input so a
    forged/absent signature is rejected (fail closed).
    """
    if not auth_token or not signature or not url:
        return False
    base = url
    try:
        for key in sorted((params or {}).keys()):
            val = params[key]
            base += key + (val if isinstance(val, str) else str(val))
    except Exception:  # noqa: BLE001 — malformed params -> reject
        return False
    mac = hmac.new(auth_token.encode("utf-8"), base.encode("utf-8"), hashlib.sha1)
    computed = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(computed, signature)


# -- TwiML builders (pure strings — no credentials required) ------------------
def twiml_gather(action_url, play_url=None, say_text=None,
                 speech_timeout="auto", language="en-US", hints=None):
    """A ``<Gather input="speech">`` turn: speak a prompt, then capture the reply.

    The prompt is an ``<Play>`` of synthesized audio when ``play_url`` is given,
    otherwise a provider ``<Say>`` of ``say_text``. The recognised speech is
    POSTed to ``action_url``. When the caller is silent the call falls through
    past the ``</Gather>`` — we redirect back to ``action_url`` so the turn loop
    can offer a graceful re-prompt / wrap-up rather than dropping the call.
    """
    inner = _prompt_markup(play_url, say_text)
    attrs = ' input="speech" method="POST" action=%s speechTimeout=%s language=%s' % (
        _xml_attr(action_url), _xml_attr(str(speech_timeout)), _xml_attr(language),
    )
    if hints:
        attrs += " hints=%s" % _xml_attr(hints if isinstance(hints, str) else ",".join(hints))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Gather%s>%s</Gather>"
        "<Redirect method=\"POST\">%s</Redirect>"
        "</Response>"
    ) % (attrs, inner, _xml_escape(action_url))


def twiml_play_hangup(play_url=None, say_text=None):
    """Speak a final line (audio or ``<Say>``) and hang up."""
    inner = _prompt_markup(play_url, say_text)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>%s<Hangup/></Response>"
    ) % inner


def twiml_say(say_text):
    """A bare ``<Say>`` response (used when TTS is unavailable — honest fallback)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Say>%s</Say><Hangup/></Response>"
    ) % _xml_escape(say_text or "")


def _prompt_markup(play_url, say_text):
    if play_url:
        return "<Play>%s</Play>" % _xml_escape(play_url)
    if say_text:
        return "<Say>%s</Say>" % _xml_escape(say_text)
    return ""
