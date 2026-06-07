"""Gmail OTP bridge — read a recent emailed one-time verification code.

Many real services email a one-time login code instead of texting it. When the
SMS path (the macOS Messages SSH bridge) has nothing, this module fetches the
most recent verification-looking email via the Gmail REST API and extracts the
numeric code. It mirrors the SMS bridge's safety posture:

- Read-only: only ``messages.list`` / ``messages.get`` are called. Nothing is
  ever sent, modified, labelled, marked-read, or deleted.
- The email body is NEVER logged. Only the extracted code (and a candidate
  count) ever leave this module — the same discipline as the SMS path.
- Bounded by recency (``after:`` epoch filter) and a small result cap, so a
  large mailbox can never blow up the request.

Credentials come from the Replit Gmail connector via the connector proxy. The
OAuth access token is fetched fresh on every call (never cached) — tokens
expire and the proxy refreshes them.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Replit Gmail connector name used with the credential proxy.
GMAIL_CONNECTOR = "google-mail"

_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Most one-time codes are 4-8 digits, optionally grouped 3-3. Mirrors the SMS
#: bridge's pattern so emailed and texted codes are extracted identically.
_CODE_RE = re.compile(r"\b(\d{3}[\s-]?\d{3}|\d{4,8})\b")

#: Words that mark a verification/OTP email. Used both to scope the Gmail search
#: (bounded recall) and to prefer the right message when several are recent.
_OTP_TERMS = [
    "verification", "verify", "one-time", "one time", "otp",
    "security code", "login code", "sign-in code", "sign in code",
    "authentication code", "your code", "passcode", "access code",
    "confirmation code",
]

#: Hard caps so a busy inbox can never balloon the request.
_MAX_RESULTS = 10
_MAX_BODY_CHARS = 4000


class GmailBridgeError(RuntimeError):
    """Raised for connection/auth/transport problems talking to Gmail."""


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _b64url_decode(data: str) -> bytes:
    if not data:
        return b""
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except Exception:
        return b""


class GmailOtpReader:
    """Read-only Gmail reader scoped to recent verification-code emails."""

    def __init__(self, access_token: Optional[str] = None):
        self._token = access_token

    def _get_token(self) -> Optional[str]:
        if self._token:
            return self._token
        # Fetched fresh per call from the connector proxy; never cached here.
        from src.web.connectors import get_access_token
        return get_access_token(GMAIL_CONNECTOR)

    def available(self) -> bool:
        """True when Gmail is connected and a token can be obtained."""
        return bool(self._get_token())

    def _api_get(self, token: str, path: str, params: Optional[dict] = None) -> dict:
        url = "%s%s" % (_API_BASE, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer %s" % token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # Never echo the response body (may contain message content); report
            # only the status so callers can fail honestly.
            raise GmailBridgeError(
                "Gmail API returned HTTP %s. The connection may need to be "
                "re-authorized." % e.code
            )
        except Exception as e:
            raise GmailBridgeError(
                "Could not reach the Gmail API (%s)." % type(e).__name__
            )

    @staticmethod
    def _build_query(within_minutes: int) -> str:
        after = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        epoch = int(after.timestamp())
        terms = " OR ".join('"%s"' % t for t in _OTP_TERMS)
        return "after:%d (%s)" % (epoch, terms)

    @staticmethod
    def _message_text(msg: dict) -> str:
        """Return subject + snippet + bounded plain-text body for ``msg``.

        The combined text is used only for in-process code extraction and is
        never logged or returned to the caller.
        """
        parts: List[str] = []
        payload = msg.get("payload") or {}
        headers = payload.get("headers") or []
        for h in headers:
            if (h.get("name") or "").lower() == "subject":
                parts.append(_decode_header(h.get("value")))
                break
        snippet = msg.get("snippet")
        if snippet:
            import html
            parts.append(html.unescape(snippet))

        body_text = ""
        stack = [payload]
        while stack and len(body_text) < _MAX_BODY_CHARS:
            part = stack.pop()
            mime = part.get("mimeType") or ""
            body = part.get("body") or {}
            data = body.get("data")
            if mime == "text/plain" and data:
                body_text += _b64url_decode(data).decode("utf-8", "replace")
            for sub in (part.get("parts") or []):
                stack.append(sub)
        if not body_text:
            # Fall back to HTML if no text/plain part carried the code.
            stack = [payload]
            while stack and len(body_text) < _MAX_BODY_CHARS:
                part = stack.pop()
                if (part.get("mimeType") or "") == "text/html":
                    data = (part.get("body") or {}).get("data")
                    if data:
                        raw = _b64url_decode(data).decode("utf-8", "replace")
                        body_text += re.sub(r"<[^>]+>", " ", raw)
                for sub in (part.get("parts") or []):
                    stack.append(sub)
        if body_text:
            parts.append(body_text[:_MAX_BODY_CHARS])
        return "\n".join(parts)

    @staticmethod
    def extract_codes(text: str) -> List[str]:
        """Pull plausible verification codes from ``text``, in order."""
        found: List[str] = []
        for m in _CODE_RE.findall(text or ""):
            normalized = re.sub(r"[\s-]", "", m)
            if 4 <= len(normalized) <= 8 and normalized not in found:
                found.append(normalized)
        return found

    def fetch_recent_code(self, within_minutes: int = 10) -> str:
        """Read the most recent verification email and extract its code.

        Returns a human-readable string. On success it begins with
        ``Most recent verification code:`` followed by the code, mirroring the
        SMS bridge so the same caller-side parsing works. When nothing matches
        it returns an honest ``No ...`` message; failures raise
        :class:`GmailBridgeError`.
        """
        within_minutes = max(1, min(int(within_minutes or 10), 120))
        token = self._get_token()
        if not token:
            raise GmailBridgeError(
                "Gmail is not connected. Connect Gmail so MyDude can read "
                "emailed verification codes."
            )

        listing = self._api_get(
            token, "/messages",
            {"q": self._build_query(within_minutes), "maxResults": _MAX_RESULTS},
        )
        messages = listing.get("messages") or []
        if not messages:
            return "No recent verification email was found in the last %d minute(s)." % within_minutes

        # Gmail returns newest first. Walk until we find a code, preferring
        # messages whose text actually mentions an OTP term.
        scanned = 0
        for ref in messages:
            mid = ref.get("id")
            if not mid:
                continue
            try:
                msg = self._api_get(token, "/messages/%s" % mid, {"format": "full"})
            except GmailBridgeError:
                continue
            scanned += 1
            text = self._message_text(msg)
            low = text.lower()
            codes = self.extract_codes(text)
            if codes and any(term in low for term in _OTP_TERMS):
                return "Most recent verification code: %s\n(candidates: %s)" % (
                    codes[0], ", ".join(codes[:5])
                )
        # No OTP-term-anchored code; fall back to the first code in the newest
        # message if any, so a terse "123456 is your code" still works.
        for ref in messages[:scanned or 1]:
            mid = ref.get("id")
            if not mid:
                continue
            try:
                msg = self._api_get(token, "/messages/%s" % mid, {"format": "full"})
            except GmailBridgeError:
                continue
            codes = self.extract_codes(self._message_text(msg))
            if codes:
                return "Most recent verification code: %s\n(candidates: %s)" % (
                    codes[0], ", ".join(codes[:5])
                )
        return "No verification code was found in recent email."
