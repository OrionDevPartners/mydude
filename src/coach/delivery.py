"""Provider-agnostic outbound delivery for the secretary (email / SMS / booking).

Governance pillars #2/#3: the call site (``secretary.confirm_action``) calls
``dispatch(channel, ...)`` and never names a provider. Credentials are sourced via
the connector proxy first, then the vault/env fallback. When a channel has no
usable provider we FAIL LOUD (``DeliveryNotConfigured``) — we never fake a "sent".

Channels (one provider each, swappable behind this module):
  - email    -> Resend
  - sms      -> Twilio
  - calendar -> Google Calendar (connector token / vault)
"""
import logging

import httpx

from src.web.connectors import get_connection_settings, get_access_token
from src.providers.secrets import get_secret, get_env

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


class DeliveryNotConfigured(RuntimeError):
    """Raised when a channel has no usable provider credentials (fail loud)."""


class DeliveryError(RuntimeError):
    """Raised when a configured provider rejects or fails the send."""


# --------------------------------------------------------------------------- #
# Email (Resend)
# --------------------------------------------------------------------------- #

def _resend_creds():
    settings = get_connection_settings("resend")
    key = sender = None
    if settings:
        key = settings.get("api_key") or settings.get("apiKey")
        sender = settings.get("from") or settings.get("from_email")
    key = key or get_secret("RESEND_API_KEY")
    sender = sender or get_env("RESEND_FROM") or get_env("EMAIL_FROM")
    return key, sender


def email_status():
    key, sender = _resend_creds()
    ok = bool(key and sender)
    return {
        "channel": "email",
        "configured": ok,
        "provider": "resend" if ok else None,
        "detail": "Email ready (resend)." if ok else
                  "Add RESEND_API_KEY and RESEND_FROM (sender) to enable email.",
    }


def send_email(to, subject, body):
    key, sender = _resend_creds()
    if not key or not sender:
        raise DeliveryNotConfigured(
            "Email is not configured. Add RESEND_API_KEY and RESEND_FROM (sender) "
            "to the vault."
        )
    if not to:
        raise DeliveryError("Email recipient is required.")
    payload = {"from": sender, "to": [to], "subject": subject or "(no subject)",
               "text": body or ""}
    try:
        resp = httpx.post("https://api.resend.com/emails", json=payload, timeout=_TIMEOUT,
                          headers={"Authorization": "Bearer %s" % key,
                                   "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise DeliveryError("Email send failed: %s" % e)
    if resp.status_code in (401, 403):
        raise DeliveryNotConfigured(
            "Email provider rejected the API key (HTTP %d). Update RESEND_API_KEY."
            % resp.status_code)
    if resp.status_code >= 400:
        raise DeliveryError("Email provider error (HTTP %d): %s"
                            % (resp.status_code, resp.text[:200]))
    data = resp.json() if resp.content else {}
    return {"provider": "resend",
            "detail": "Email sent (id=%s)." % (data.get("id") or "ok")}


# --------------------------------------------------------------------------- #
# SMS (Twilio)
# --------------------------------------------------------------------------- #

def _twilio_creds():
    settings = get_connection_settings("twilio")
    sid = tok = frm = None
    if settings:
        sid = settings.get("account_sid") or settings.get("accountSid")
        tok = settings.get("auth_token") or settings.get("authToken")
        frm = settings.get("from") or settings.get("from_number")
    sid = sid or get_secret("TWILIO_ACCOUNT_SID")
    tok = tok or get_secret("TWILIO_AUTH_TOKEN")
    frm = frm or get_env("TWILIO_FROM") or get_env("TWILIO_FROM_NUMBER")
    return sid, tok, frm


def sms_status():
    sid, tok, frm = _twilio_creds()
    ok = bool(sid and tok and frm)
    return {
        "channel": "sms",
        "configured": ok,
        "provider": "twilio" if ok else None,
        "detail": "SMS ready (twilio)." if ok else
                  "Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM to "
                  "enable SMS.",
    }


def send_sms(to, body):
    sid, tok, frm = _twilio_creds()
    if not sid or not tok or not frm:
        raise DeliveryNotConfigured(
            "SMS is not configured. Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
            "TWILIO_FROM to the vault."
        )
    if not to:
        raise DeliveryError("SMS recipient is required.")
    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid
    try:
        resp = httpx.post(url, data={"From": frm, "To": to, "Body": body or ""},
                          auth=(sid, tok), timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise DeliveryError("SMS send failed: %s" % e)
    if resp.status_code in (401, 403):
        raise DeliveryNotConfigured(
            "SMS provider rejected the credentials (HTTP %d). Update Twilio keys."
            % resp.status_code)
    if resp.status_code >= 400:
        raise DeliveryError("SMS provider error (HTTP %d): %s"
                            % (resp.status_code, resp.text[:200]))
    data = resp.json() if resp.content else {}
    return {"provider": "twilio",
            "detail": "SMS sent (sid=%s)." % (data.get("sid") or "ok")}


# --------------------------------------------------------------------------- #
# Calendar booking (Google Calendar)
# --------------------------------------------------------------------------- #

def _calendar_token():
    try:
        token = (get_access_token("google-calendar")
                 or get_access_token("googlecalendar"))
    except Exception:
        token = None
    return token or get_secret("GOOGLE_CALENDAR_ACCESS_TOKEN")


def calendar_status():
    ok = bool(_calendar_token())
    return {
        "channel": "calendar",
        "configured": ok,
        "provider": "google-calendar" if ok else None,
        "detail": "Calendar ready (google-calendar)." if ok else
                  "Connect Google Calendar or add GOOGLE_CALENDAR_ACCESS_TOKEN to "
                  "enable booking.",
    }


def create_booking(payload):
    token = _calendar_token()
    if not token:
        raise DeliveryNotConfigured(
            "Calendar booking is not configured. Connect Google Calendar or add "
            "GOOGLE_CALENDAR_ACCESS_TOKEN to the vault."
        )
    if not isinstance(payload, dict):
        raise DeliveryError("Booking payload is required.")
    summary, start, end = payload.get("summary"), payload.get("start"), payload.get("end")
    if not summary or not start or not end:
        raise DeliveryError("Booking requires 'summary', 'start' and 'end' (RFC3339).")
    body = {"summary": summary, "description": payload.get("description") or "",
            "start": {"dateTime": start}, "end": {"dateTime": end}}
    if payload.get("location"):
        body["location"] = payload["location"]
    attendees = payload.get("attendees")
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees if a]
    cal_id = payload.get("calendar_id") or "primary"
    url = "https://www.googleapis.com/calendar/v3/calendars/%s/events" % cal_id
    try:
        resp = httpx.post(url, json=body, timeout=_TIMEOUT,
                          headers={"Authorization": "Bearer %s" % token,
                                   "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise DeliveryError("Calendar request failed: %s" % e)
    if resp.status_code in (401, 403):
        raise DeliveryNotConfigured(
            "Calendar rejected the request (HTTP %d). Reconnect Google Calendar."
            % resp.status_code)
    if resp.status_code >= 400:
        raise DeliveryError("Calendar API error (HTTP %d): %s"
                            % (resp.status_code, resp.text[:200]))
    data = resp.json() if resp.content else {}
    return {"provider": "google-calendar",
            "detail": "Event created (id=%s)." % (data.get("id") or "ok")}


# --------------------------------------------------------------------------- #
# Dispatch + combined status
# --------------------------------------------------------------------------- #

_STATUS_FN = {"email": email_status, "sms": sms_status, "calendar": calendar_status}


def channel_configured(channel):
    fn = _STATUS_FN.get(channel)
    return bool(fn and fn()["configured"])


def dispatch(channel, recipient=None, subject=None, body=None, payload=None):
    if channel == "email":
        return send_email(recipient, subject, body)
    if channel == "sms":
        return send_sms(recipient, body)
    if channel == "calendar":
        return create_booking(payload or {})
    raise DeliveryError("Unknown delivery channel '%s'." % channel)


def delivery_status():
    return {"email": email_status(), "sms": sms_status(), "calendar": calendar_status()}
