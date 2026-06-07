"""Email bridge — read recent billing/receipt emails over IMAP (read-only).

Subscription discovery from browser history misses anything the user pays for
but rarely opens in a browser (annual plans, app-store-billed subscriptions,
niche SaaS). Most of those still send a receipt or renewal email. This module
connects to the user's mailbox over IMAP, pulls the most recent
billing/receipt-looking messages, and returns a *structured* summary (sender,
subject, a bounded snippet, date). It performs the *transport* only:

- It never deletes, moves, marks, or sends anything — the mailbox is opened
  read-only (``select(..., readonly=True)``).
- Merchant / amount / cadence extraction and catalog matching happen upstream in
  ``src/subscriptions/discovery.py``; this layer just fetches text.

Connection details come from the credential vault (synced to env vars). The
``imaplib`` import is lazy so the app boots fine with this capability disabled
and the vault empty.
"""
from __future__ import annotations

import email
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import List, Optional

from src.providers.secrets import get_secret, get_env

logger = logging.getLogger(__name__)

#: Subjects that tend to mark a recurring-billing email. Matched case-insensitive.
RECEIPT_SUBJECT_TERMS = [
    "receipt",
    "invoice",
    "payment",
    "subscription",
    "renew",
    "your order",
    "billed",
    "billing",
    "auto-renewal",
    "membership",
]

#: Hard caps so a huge mailbox can never blow up the request.
MAX_MESSAGES = 100
MAX_BODY_CHARS = 2000


class EmailBridgeError(RuntimeError):
    """Raised for connection/auth/config problems talking to the mailbox."""


@dataclass
class EmailConfig:
    host: Optional[str]
    port: int
    user: Optional[str]
    password: Optional[str]
    mailbox: str = "INBOX"
    use_ssl: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)


def _truthy(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_email_config() -> EmailConfig:
    """Read IMAP connection details from the environment (vault-synced)."""
    port_raw = get_env("IMAP_PORT", "993") or "993"
    try:
        port = int(port_raw)
    except ValueError:
        port = 993
    return EmailConfig(
        host=get_secret("IMAP_HOST") or get_env("IMAP_HOST"),
        port=port,
        user=get_secret("IMAP_USER") or get_env("IMAP_USER"),
        password=get_secret("IMAP_PASSWORD"),
        mailbox=get_env("IMAP_MAILBOX", "INBOX") or "INBOX",
        use_ssl=_truthy(get_env("IMAP_SSL", "true"), True),
    )


def _decode(value: Optional[str]) -> str:
    """Decode a possibly RFC2047-encoded header into a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg) -> str:
    """Return a bounded plain-text body for ``msg`` (prefers text/plain)."""
    text = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if ctype == "text/plain" and "attachment" not in disp.lower():
                    payload = part.get_payload(decode=True)
                    if payload:
                        text = payload.decode(
                            part.get_content_charset() or "utf-8", "replace"
                        )
                        break
            if not text:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            text = payload.decode(
                                part.get_content_charset() or "utf-8", "replace"
                            )
                            break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
    except Exception as e:  # never let one malformed message break the scan
        logger.debug("Failed to extract email body: %s", e)
        text = ""
    return text[:MAX_BODY_CHARS]


class EmailReceiptReader:
    """Read-only IMAP reader scoped to recent receipt/billing emails."""

    def __init__(self, config: Optional[EmailConfig] = None):
        self.config = config or load_email_config()

    def available(self) -> bool:
        return self.config.configured

    def _connect(self):
        import imaplib

        cfg = self.config
        if not cfg.configured:
            raise EmailBridgeError(
                "Email bridge is not configured. Add IMAP_HOST, IMAP_USER and "
                "IMAP_PASSWORD in the vault."
            )
        try:
            if cfg.use_ssl:
                client = imaplib.IMAP4_SSL(cfg.host, cfg.port)
            else:
                client = imaplib.IMAP4(cfg.host, cfg.port)
        except Exception as e:
            raise EmailBridgeError(
                "Could not reach the mail server %s:%d (%s: %s)."
                % (cfg.host, cfg.port, type(e).__name__, e)
            )
        try:
            client.login(cfg.user, cfg.password)
        except Exception as e:
            try:
                client.logout()
            except Exception:
                pass
            raise EmailBridgeError(
                "IMAP login failed for %s (%s). Check the username/password — "
                "many providers require an app-specific password." % (cfg.user, type(e).__name__)
            )
        return client

    def read_receipts(self, limit: int = 50, lookback_days: int = 365) -> List[dict]:
        """Return recent receipt-like emails as a list of dicts.

        Each dict has ``from``, ``subject``, ``date`` and a bounded ``body``.
        The mailbox is opened read-only; nothing is modified. ``limit`` and the
        message cap bound how much is fetched.
        """
        limit = max(1, min(int(limit or 50), MAX_MESSAGES))
        lookback_days = max(1, min(int(lookback_days or 365), 1095))
        client = self._connect()
        try:
            typ, _ = client.select(self.config.mailbox, readonly=True)
            if typ != "OK":
                raise EmailBridgeError(
                    "Could not open mailbox '%s' (read-only)." % self.config.mailbox
                )
            since = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")

            # Union per-term SUBJECT searches restricted to the lookback window.
            # IMAP nested-OR queries are brittle across servers, so we run one
            # search per term and merge the resulting message ids.
            seen_ids: List[bytes] = []
            id_set = set()
            for term in RECEIPT_SUBJECT_TERMS:
                try:
                    typ, data = client.search(None, "SINCE", since, "SUBJECT", term)
                except Exception as e:
                    logger.debug("IMAP search for '%s' failed: %s", term, e)
                    continue
                if typ != "OK" or not data or not data[0]:
                    continue
                for mid in data[0].split():
                    if mid not in id_set:
                        id_set.add(mid)
                        seen_ids.append(mid)

            if not seen_ids:
                return []

            # Newest first, then fetch up to the limit.
            seen_ids.sort(key=lambda b: int(b) if b.isdigit() else 0, reverse=True)
            seen_ids = seen_ids[:limit]

            results: List[dict] = []
            for mid in seen_ids:
                try:
                    typ, msg_data = client.fetch(mid, "(RFC822)")
                except Exception as e:
                    logger.debug("IMAP fetch %s failed: %s", mid, e)
                    continue
                if typ != "OK" or not msg_data:
                    continue
                raw = None
                for part in msg_data:
                    if isinstance(part, tuple) and len(part) == 2:
                        raw = part[1]
                        break
                if not raw:
                    continue
                try:
                    msg = email.message_from_bytes(raw)
                except Exception:
                    continue
                from_name, from_addr = parseaddr(_decode(msg.get("From")))
                results.append({
                    "from": from_addr or from_name or "",
                    "from_name": from_name or "",
                    "subject": _decode(msg.get("Subject")),
                    "date": _decode(msg.get("Date")),
                    "body": _extract_body(msg),
                })
            return results
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
