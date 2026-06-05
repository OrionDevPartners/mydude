"""Subscription discovery — inference from reachable signals, not magic.

The only signal MyDude can actually reach about the user's spending habits is
the browser history on the bridged Mac (read read-only over SSH). We pull recent
history, extract hostnames, and match them against a catalog of known
subscription/billing domains. Everything produced here is a *candidate* the user
must confirm — MyDude cannot read the Keychain or card data, so this is never a
guaranteed-complete list.
"""
import logging
from urllib.parse import urlparse

from src.subscriptions.catalog import match_host

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
