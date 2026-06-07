"""Tests for email-receipt subscription discovery.

These cover the new email source end-to-end *minus* the network: catalog
merchant matching, receipt parsing (merchant + amount + cadence extraction and
de-duplication), cross-source merging by domain, the governance gate, and the
honest "not configured" reporting path. No IMAP server, credentials, or real
mailbox are required — the broker is faked.

Runnable two ways:
  * ``python tests/test_subscription_email.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_subscription_email.py``   (test_* functions; no plugins needed)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.subscriptions.catalog import match_merchant
from src.subscriptions.discovery import (
    parse_receipts,
    merge_candidates,
    discover_from_email,
)
from src.swarm.policy import PolicyEngine
from src.swarm.integrations import Integrations


# -- catalog merchant matching -----------------------------------------------

def test_match_merchant_by_sender_domain():
    entry = match_merchant(from_addr="no-reply@netflix.com", text="Your receipt")
    assert entry and entry["slug"] == "netflix", entry
    # Subdomain senders resolve too.
    entry = match_merchant(from_addr="billing@accounts.spotify.com", text="")
    assert entry and entry["slug"] == "spotify", entry


def test_match_merchant_by_name_keyword():
    entry = match_merchant(from_addr="receipts@paddle.net",
                           text="Your Dropbox subscription has renewed")
    assert entry and entry["slug"] == "dropbox", entry


def test_match_merchant_none_for_unknown():
    assert match_merchant(from_addr="hi@some-random-shop.example",
                          text="Thanks for your order") is None


# -- receipt parsing ----------------------------------------------------------

def _msg(frm, subject, body=""):
    return {"from": frm, "subject": subject, "body": body, "date": "today"}


def test_parse_receipts_extracts_amount_and_cadence():
    raw = json.dumps([
        _msg("billing@netflix.com", "Your receipt — $15.49 this month"),
    ])
    cands = parse_receipts(raw)
    assert len(cands) == 1, cands
    c = cands[0]
    assert c["slug"] == "netflix"
    assert c["source"] == "email_receipt"
    assert c["est_cost"] == "$15.49/mo", c["est_cost"]
    assert c["cadence"] == "monthly"


def test_parse_receipts_yearly_cadence_and_body_fallback():
    raw = json.dumps([
        _msg("no-reply@adobe.com", "Payment confirmation",
             "You were billed USD 59.99 for your annual plan."),
    ])
    cands = parse_receipts(raw)
    assert len(cands) == 1
    assert cands[0]["est_cost"] == "$59.99/yr", cands[0]["est_cost"]


def test_parse_receipts_dedups_by_slug_and_counts_hits():
    raw = json.dumps([
        _msg("billing@spotify.com", "Receipt $11.99 monthly"),
        _msg("billing@spotify.com", "Receipt $11.99 monthly"),
    ])
    cands = parse_receipts(raw)
    assert len(cands) == 1
    assert cands[0]["hits"] == 2, cands[0]


def test_parse_receipts_handles_bad_input():
    assert parse_receipts("not json") == []
    assert parse_receipts(json.dumps({"not": "a list"})) == []
    assert parse_receipts(json.dumps([])) == []


# -- cross-source merge -------------------------------------------------------

def test_merge_candidates_dedups_by_domain():
    history = [{"slug": "netflix", "name": "Netflix", "domain": "netflix.com",
                "source": "browser_history", "hits": 1}]
    email = [
        {"slug": "netflix", "name": "Netflix", "domain": "netflix.com",
         "est_cost": "$15.49/mo", "source": "email_receipt", "hits": 1},
        {"slug": "adobe", "name": "Adobe Creative Cloud", "domain": "adobe.com",
         "est_cost": "$59.99/yr", "source": "email_receipt", "hits": 1},
    ]
    merged = merge_candidates(history, email)
    assert len(merged) == 2, merged
    netflix = next(m for m in merged if m["domain"] == "netflix.com")
    # First source (history) wins core fields; email fills missing est_cost.
    assert netflix["est_cost"] == "$15.49/mo"
    assert netflix["source"] == "browser_history+email_receipt", netflix["source"]
    assert netflix["hits"] == 2


# -- governance gate ----------------------------------------------------------

def test_policy_blocks_email_when_disabled():
    os.environ.pop("ENABLE_EMAIL_CAPABILITY", None)
    decision = PolicyEngine().evaluate("imap_read_receipts", {})
    assert not decision.allowed
    assert "disabled" in decision.reason.lower()


def test_policy_allows_email_when_enabled():
    os.environ["ENABLE_EMAIL_CAPABILITY"] = "true"
    try:
        decision = PolicyEngine().evaluate("imap_read_receipts", {})
        assert decision.allowed, decision.reason
    finally:
        os.environ.pop("ENABLE_EMAIL_CAPABILITY", None)


# -- honest "not configured" reporting ---------------------------------------

class _FakeDecision:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason


class _FakeResult:
    def __init__(self, allowed, output, reason=""):
        self.decision = _FakeDecision(allowed, reason)
        self.output = output


class _FakeBroker:
    def __init__(self, result):
        self._result = result

    async def request(self, capability, params):
        return self._result


def test_discover_from_email_reports_not_configured():
    broker = _FakeBroker(_FakeResult(
        True, "Email not configured. Add IMAP_HOST, IMAP_USER and IMAP_PASSWORD in the vault."))
    cands, msg = asyncio.run(discover_from_email(broker))
    assert cands == []
    assert "could not read receipts" in msg.lower()


def test_discover_from_email_reports_blocked():
    broker = _FakeBroker(_FakeResult(False, None, reason="Email capability is disabled."))
    cands, msg = asyncio.run(discover_from_email(broker))
    assert cands == []
    assert "blocked" in msg.lower()


def test_discover_from_email_happy_path():
    raw = json.dumps([_msg("billing@netflix.com", "Receipt $15.49 monthly")])
    broker = _FakeBroker(_FakeResult(True, raw))
    cands, msg = asyncio.run(discover_from_email(broker))
    assert len(cands) == 1 and cands[0]["slug"] == "netflix"
    assert "found 1 candidate" in msg.lower()


def test_integrations_email_not_configured_is_honest():
    # With no IMAP_* env vars, the integration must report cleanly (no crash).
    for var in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        os.environ.pop(var, None)
    out = asyncio.run(Integrations().imap_read_receipts({"source": "test"}))
    assert out.startswith("Email not configured"), out


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
