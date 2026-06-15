"""Subscription discovery — inference from reachable signals, not magic.

MyDude infers candidate subscriptions from two reachable signals:

1. **Browser history** on the bridged Mac (read read-only over SSH). We extract
   hostnames and match them against a catalog of known subscription/billing
   domains.
2. **Billing/receipt emails** read read-only over IMAP. We match the sender
   domain (or a distinctive service name in the subject/body) to the catalog and
   pull out the amount and billing cadence when present.

Everything produced here is a *candidate* the user must confirm — MyDude cannot
read the Keychain or card data, so neither source is a guaranteed-complete list.
Candidates from both sources are merged and de-duplicated by domain.
"""
import json
import logging
import re
from urllib.parse import urlparse

from src.subscriptions.catalog import match_host, match_merchant

logger = logging.getLogger(__name__)


def _host_from_line(line):
    """Extract a hostname from one ``url | title | date`` history row."""
    first = line.split("|", 1)[0].strip()
    if not first:
        return None
    if "://" not in first:
        first = "http://" + first
    try:
        return (urlparse(first).hostname or "").lower()
    except Exception:
        return None


def parse_history(raw_text):
    """Parse SSH history output into an ordered, de-duplicated list of candidate
    dicts derived from catalog matches. Newest-first order is preserved."""
    candidates = {}
    for line in (raw_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        host = _host_from_line(line)
        if not host:
            continue
        entry = match_host(host)
        if not entry:
            continue
        slug = entry["slug"]
        if slug in candidates:
            candidates[slug]["hits"] += 1
            continue
        candidates[slug] = {
            "slug": slug,
            "name": entry["name"],
            "domain": entry["domains"][0],
            "login_url": entry["login_url"],
            "account_url": entry["account_url"],
            "est_cost": entry.get("est_cost"),
            "source": "browser_history",
            "hits": 1,
        }
    return list(candidates.values())


async def discover_from_history(broker, browser="chrome", limit=100):
    """Run discovery through the governed broker.

    Returns ``(candidates, status_message)``. ``candidates`` may be empty; the
    status message explains why (SSH disabled, nothing matched, error) so the UI
    can be honest rather than implying completeness.
    """
    res = await broker.request(
        "ssh_read_history",
        {"browser": browser, "limit": limit, "source": "subscriptions-discovery"},
    )
    if not res.decision.allowed:
        return [], (
            "Discovery needs the SSH bridge to read your browser history, but it "
            "is blocked: %s" % res.decision.reason
        )
    raw = res.output or ""
    if raw.startswith("SSH bridge error:") or raw.startswith("No history"):
        return [], (
            "Could not read browser history from the bridge host. %s" % raw
        )
    candidates = parse_history(raw)
    if not candidates:
        return [], (
            "Read your %s history but found no known subscription services. This "
            "is best-effort — MyDude can only infer from sites you've visited, so "
            "add anything it missed manually." % browser
        )
    return candidates, (
        "Found %d candidate subscription(s) from your %s history. Confirm the "
        "ones you actually pay for." % (len(candidates), browser)
    )


# -- email receipts -----------------------------------------------------------

# Money amounts: $/€/£ optionally with a code, e.g. "$12.99", "USD 12.99", "£9".
_AMOUNT_RE = re.compile(
    r"(?:(?P<sym>[$€£])\s?|(?P<code>USD|EUR|GBP|US)\s?\$?\s?)"
    r"(?P<num>\d{1,4}(?:[.,]\d{2})?)",
    re.IGNORECASE,
)
_CADENCE_PATTERNS = [
    ("yearly", re.compile(r"\b(year(ly)?|annual(ly)?|/\s*yr|per\s+year|12\s+months)\b", re.I)),
    ("monthly", re.compile(r"\b(month(ly)?|/\s*mo|per\s+month)\b", re.I)),
    ("weekly", re.compile(r"\b(week(ly)?|/\s*wk|per\s+week)\b", re.I)),
]
_SYM = {"$": "$", "€": "€", "£": "£", "USD": "$", "US": "$", "EUR": "€", "GBP": "£"}


def _extract_amount(text):
    """Return a normalised amount string (e.g. ``$12.99``) or None."""
    if not text:
        return None
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    sym = m.group("sym") or _SYM.get((m.group("code") or "").upper(), "$")
    return "%s%s" % (sym, m.group("num"))


def _extract_cadence(text):
    """Return ``monthly`` / ``yearly`` / ``weekly`` if the text hints it, else None."""
    if not text:
        return None
    for label, pat in _CADENCE_PATTERNS:
        if pat.search(text):
            return label
    return None


def _cost_label(amount, cadence):
    """Combine amount + cadence into a human est-cost string, honestly partial."""
    if not amount:
        return None
    suffix = {"monthly": "/mo", "yearly": "/yr", "weekly": "/wk"}.get(cadence, "")
    return "%s%s" % (amount, suffix)


def parse_cost_string(text):
    """Best-effort split of a cost string/text into ``(amount, currency, cadence)``.

    ``amount`` is a float, ``currency`` a symbol (``$``/``€``/``£``), and
    ``cadence`` one of ``monthly``/``yearly``/``weekly`` — any of which may be
    ``None`` when the text doesn't carry it. Robust to both catalog/display
    strings like ``"$9.99/mo"`` and free-form receipt text, so it serves both
    fresh discovery and the best-effort backfill of legacy ``est_cost`` rows.
    """
    if not text:
        return None, None, None
    amount = None
    currency = None
    m = _AMOUNT_RE.search(text)
    if m:
        currency = m.group("sym") or _SYM.get((m.group("code") or "").upper(), "$")
        num = (m.group("num") or "").replace(",", ".")
        try:
            amount = float(num)
        except ValueError:
            amount = None
    return amount, currency, _extract_cadence(text)


# Words that, alongside a money amount, mark an email as a billing/receipt rather
# than marketing. Used only to surface *unmatched* receipts so genuine
# subscriptions from merchants not yet in the catalog are not silently dropped.
_BILLING_CONTEXT_RE = re.compile(
    r"\b(receipt|invoice|subscription|subscribe(d)?|renew(al|ed|s|ing)?|"
    r"bill(ed|ing)?|payment|paid|charged|transaction|membership|"
    r"auto[\s-]?renew|order\s+confirmation|your\s+(order|purchase)|"
    r"thanks?\s+for\s+your\s+(payment|purchase|order))\b",
    re.I,
)

# Consumer mailbox providers are *senders*, never merchants — a receipt
# forwarded from a personal address must not be surfaced as a subscription.
_CONSUMER_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com",
    "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com", "fastmail.com",
}

# A few second-level public suffixes so ``billing.shop.co.uk`` collapses to
# ``shop.co.uk`` rather than ``co.uk``. Best-effort, not an exhaustive PSL.
_TWO_LEVEL_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "co.jp", "co.kr", "com.br", "com.mx", "co.in", "co.za",
}


def _sender_domain(from_addr):
    """Extract the lowercase domain from a ``From:`` value, or None.

    Handles ``Name <user@host>`` forms and trailing punctuation. Returns None
    when there is no usable domain (no ``@`` or no dot in the host part)."""
    addr = (from_addr or "").strip().lower()
    if "<" in addr and ">" in addr:
        addr = addr[addr.find("<") + 1:addr.find(">")].strip()
    if "@" not in addr:
        return None
    domain = addr.rsplit("@", 1)[-1].strip().strip(">").strip()
    domain = domain.split()[0] if domain else domain
    if "." not in domain:
        return None
    return domain


def _registrable_domain(host):
    """Collapse a host to its registrable domain (best-effort, no PSL dep)."""
    parts = (host or "").split(".")
    if len(parts) <= 2:
        return host
    last_two = ".".join(parts[-2:])
    if last_two in _TWO_LEVEL_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def _name_from_domain(reg_domain):
    """Human-ish merchant name from a registrable domain (``foo.com`` -> ``Foo``)."""
    label = (reg_domain or "").split(".")[0]
    return label.capitalize() if label else (reg_domain or "Unknown merchant")


def _looks_like_billing(text):
    """True when text reads like a real receipt: a money amount + billing words."""
    if not text:
        return False
    return bool(_extract_amount(text)) and bool(_BILLING_CONTEXT_RE.search(text))


def parse_receipts(raw):
    """Parse the IMAP reader's JSON output into candidate dicts.

    ``raw`` is the JSON list produced by the ``imap_read_receipts`` capability
    (each item has ``from``, ``subject``, ``body``, ``date``). Each receipt is
    matched to the catalog by sender domain or a distinctive service name; the
    amount and cadence are extracted best-effort. Candidates are de-duplicated by
    catalog slug, newest-first order preserved, and ``hits`` counts repeats.

    Receipts that clearly read like billing (a money amount plus billing words)
    but match no catalog entry are *not* dropped: they are surfaced as
    ``unknown`` candidates keyed by the sender's registrable domain, so a real
    subscription from a merchant MyDude doesn't know yet still reaches the user
    to confirm or dismiss. ``unknown`` candidates carry ``unknown=True``, a
    ``source`` of ``email_receipt_unknown`` and no login/account URLs.
    """
    try:
        messages = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (ValueError, TypeError):
        return []
    if not isinstance(messages, list):
        return []

    candidates = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        from_addr = msg.get("from") or ""
        subject = msg.get("subject") or ""
        body = msg.get("body") or ""
        blob = "%s\n%s" % (subject, body)
        entry = match_merchant(from_addr=from_addr, text=blob)
        amount = _extract_amount(subject) or _extract_amount(body)
        cadence = _extract_cadence(subject) or _extract_cadence(body)
        receipt_cost = _cost_label(amount, cadence)
        if entry:
            slug = entry["slug"]
            if slug in candidates:
                candidates[slug]["hits"] += 1
                continue
            candidates[slug] = {
                "slug": slug,
                "name": entry["name"],
                "domain": entry["domains"][0],
                "login_url": entry["login_url"],
                "account_url": entry["account_url"],
                "est_cost": receipt_cost or entry.get("est_cost"),
                "cadence": cadence,
                # True only when the cost/cadence came from the receipt itself (not a
                # catalog default), so the confirm flow can prefer it over a guess.
                "cost_from_receipt": bool(receipt_cost),
                "source": "email_receipt",
                "unknown": False,
                "hits": 1,
            }
            continue

        # No catalog match — surface it only if it really looks like a receipt
        # and comes from a real merchant domain (not a personal mailbox).
        if not _looks_like_billing(blob):
            continue
        host = _sender_domain(from_addr)
        if not host:
            continue
        reg = _registrable_domain(host)
        if not reg or reg in _CONSUMER_MAIL_DOMAINS:
            continue
        key = "unknown:%s" % reg
        if key in candidates:
            candidates[key]["hits"] += 1
            continue
        candidates[key] = {
            "slug": None,
            "name": _name_from_domain(reg),
            "domain": reg,
            "login_url": None,
            "account_url": None,
            "est_cost": receipt_cost,
            "cadence": cadence,
            "cost_from_receipt": bool(receipt_cost),
            "source": "email_receipt_unknown",
            "unknown": True,
            "hits": 1,
        }
    return list(candidates.values())


async def discover_from_email(broker, limit=50, lookback_days=365):
    """Run email-receipt discovery through the governed broker.

    Returns ``(candidates, status_message)``. The message is honest about why a
    run produced nothing — capability disabled, mailbox not configured, an error,
    or simply no recognised receipts — mirroring :func:`discover_from_history`.
    """
    res = await broker.request(
        "imap_read_receipts",
        {"limit": limit, "lookback_days": lookback_days, "source": "subscriptions-discovery"},
    )
    if not res.decision.allowed:
        return [], (
            "Discovery needs the email bridge to read your billing receipts, but "
            "it is blocked: %s" % res.decision.reason
        )
    raw = res.output or ""
    if raw.startswith("Email bridge error:") or raw.startswith("Email not configured"):
        return [], (
            "Could not read receipts from your mailbox. %s" % raw
        )
    candidates = parse_receipts(raw)
    if not candidates:
        return [], (
            "Read your recent billing emails but found no recognised subscription "
            "services. This is best-effort — MyDude can only match known merchants, "
            "so add anything it missed manually."
        )
    unknown = [c for c in candidates if c.get("unknown")]
    note = ""
    if unknown:
        note = (
            " %d look like billing from merchant(s) MyDude doesn't recognise yet — "
            "review and add or dismiss them." % len(unknown)
        )
    return candidates, (
        "Found %d candidate subscription(s) from your email receipts. Confirm the "
        "ones you actually pay for.%s" % (len(candidates), note)
    )


def merge_candidates(*candidate_lists):
    """Merge candidate lists from multiple sources, de-duplicating by domain.

    First occurrence of a domain wins its core fields; later duplicates only
    contribute a missing ``est_cost`` and add their source so the merged entry
    records every signal that pointed at it (e.g. ``browser_history+email_receipt``).
    Order is preserved across the inputs in the order given.
    """
    merged = {}
    order = []
    for candidates in candidate_lists:
        for cand in (candidates or []):
            domain = (cand.get("domain") or "").lower()
            key = domain or cand.get("slug") or cand.get("name")
            if not key:
                continue
            if key not in merged:
                entry = dict(cand)
                entry["sources"] = [cand.get("source")] if cand.get("source") else []
                merged[key] = entry
                order.append(key)
                continue
            existing = merged[key]
            src = cand.get("source")
            if src and src not in existing["sources"]:
                existing["sources"].append(src)
            if not existing.get("est_cost") and cand.get("est_cost"):
                existing["est_cost"] = cand["est_cost"]
            existing["hits"] = existing.get("hits", 1) + cand.get("hits", 1)
    result = []
    for key in order:
        entry = merged[key]
        if entry.get("sources"):
            entry["source"] = "+".join(entry["sources"])
        result.append(entry)
    return result
