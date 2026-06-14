"""Unit tests for finance auto-suggestions (``src/finance/suggestions.py``).

Suggestions build DRAFT QuickBooks write-backs (``categorize`` / ``create_bill``)
from REAL data only. The governing rules under test:

  * Never fabricate — only emit a payload grounded in real QBO Id/SyncToken,
    a vendor's own dominant expense account, and (for bills) exactly one
    receipt->vendor->bank-transaction match. When grounding is insufficient we
    SKIP with an audited reason instead of guessing.
  * Nothing auto-posts — every created request lands in ``pending_confirm``,
    behind the same CONFIRM gate as a hand-built write.
  * Dedupe is status-agnostic — a previously suggested target is never recreated.

Fully hermetic: a fresh in-memory SQLite DB per test, a fake QuickBooks client
and fake email reader injected via the engine's factories, and the attribution
memory-edge writer stubbed out — so there is no network and no live credentials.

Runnable two ways:
  * ``python tests/test_finance_suggestions.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_finance_suggestions.py``   (test_* functions; no plugins)
"""
import os
import sys
import json
import itertools
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src import models  # noqa: F401  (registers all tables on Base.metadata)
from src.models import (
    FinanceVendor, FinanceTransaction, FinanceWriteRequest, FinanceProject,
)
from src.finance import attribution, suggestions


# -- helpers -----------------------------------------------------------------

@contextmanager
def _patch(obj, name, value):
    missing = object()
    orig = getattr(obj, name, missing)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if orig is missing:
            delattr(obj, name)
        else:
            setattr(obj, name, orig)


def _session():
    """A fresh, isolated in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


_EXT = itertools.count(1)


class FakeQBO:
    """Stand-in for QuickBooksClient — returns canned accounts/purchases."""

    def __init__(self, accounts, purchases):
        self._accounts = accounts
        self._purchases = purchases

    def fetch_accounts(self):
        return self._accounts

    def fetch_purchases(self):
        return self._purchases


class FakeReader:
    """Stand-in for EmailReceiptReader — returns canned raw receipt dicts."""

    def __init__(self, receipts, available=True):
        self._receipts = receipts
        self._available = available

    def available(self):
        return self._available

    def read_receipts(self, limit=50, lookback_days=365):
        return list(self._receipts)


def _qbo_vendor(db, extid, name, default_project_id=None):
    v = FinanceVendor(source="quickbooks", external_id=str(extid), name=name,
                      normalized_name=attribution.normalize(name),
                      default_project_id=default_project_id)
    db.add(v)
    db.flush()
    return v


def _plaid_txn(db, name, amount, txn_date, pending=False, memo=None):
    t = FinanceTransaction(
        source="plaid", external_id="ext-%d" % next(_EXT),
        name=name, memo=memo, amount=amount, txn_date=txn_date, pending=pending,
        attribution_status="unattributed")
    db.add(t)
    db.flush()
    return t


def _expense_line(account_id, amount=10.0):
    """A QBO expense line referencing ``account_id`` (None = no AccountRef)."""
    detail = {}
    if account_id is not None:
        detail["AccountRef"] = {"value": str(account_id)}
    return {"Amount": amount, "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": detail}


def _purchase(pid, sync_token, entity_id, lines, entity_name=None):
    p = {"Id": str(pid), "SyncToken": str(sync_token), "Line": list(lines)}
    if entity_id is not None:
        ref = {"value": str(entity_id)}
        if entity_name:
            ref["name"] = entity_name
        p["EntityRef"] = ref
    return p


# Canonical chart of accounts: one real expense + one uncategorized bucket.
_ACCOUNTS = [
    {"Id": "10", "Name": "Job Materials", "AccountType": "Expense"},
    {"Id": "99", "Name": "Uncategorized Expense", "AccountType": "Expense"},
]


def _generate(db, accounts=_ACCOUNTS, purchases=None, receipts=None,
              email_available=True):
    """Run the engine with fakes; attribution memory-write stubbed for hermeticity."""
    qbo = FakeQBO(accounts, purchases or [])
    reader = FakeReader(receipts or [], available=email_available)
    with _patch(attribution, "_email_context", lambda: None), \
            _patch(attribution, "_write_edge", lambda *a, **k: None):
        return suggestions.generate_suggestions(
            db, qbo_client_factory=lambda: qbo,
            email_reader_factory=lambda: reader)


def _pending(db):
    return db.query(FinanceWriteRequest).filter(
        FinanceWriteRequest.status == "pending_confirm").all()


# -- categorize --------------------------------------------------------------

def test_categorize_suggestion_created():
    """A vendor with a dominant historical account -> one categorize draft for
    its uncategorized purchase, with a pure-QBO sparse-update payload."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            # History: vendor 5 dominantly uses account "10".
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
            # Target: uncategorized expense line, needs categorizing.
            _purchase(100, 3, "5", [_expense_line("99")], entity_name="Acme Tools"),
        ]
        res = _generate(db, purchases=purchases)

        assert res["ok"] is True and res["configured"] is True, res
        assert res["counts"]["categorize"] == 1, res["counts"]
        assert res["counts"]["create_bill"] == 0, res["counts"]

        rows = _pending(db)
        assert len(rows) == 1, len(rows)
        row = rows[0]
        assert row.kind == "categorize", row.kind
        assert row.target_external_id == "qbo_purchase:100", row.target_external_id
        assert row.status == "pending_confirm", row.status
        assert (row.summary or "").startswith("Auto-suggested:"), row.summary

        payload = json.loads(row.payload_json)
        assert payload["Id"] == "100", payload
        assert payload["SyncToken"] == "3", payload
        assert payload["sparse"] is True, payload
        # The uncategorized line was rewritten to the dominant account "10".
        line = payload["Line"][0]
        assert line["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == "10", line
    finally:
        db.close()


def test_categorize_skipped_no_account_history():
    """An uncategorized purchase whose vendor has no usable history is skipped,
    never guessed."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            _purchase(100, 3, "5", [_expense_line("99")], entity_name="Acme Tools"),
        ]
        res = _generate(db, purchases=purchases)

        assert res["counts"]["total"] == 0, res["counts"]
        assert res["skipped"].get("no_account_history") == 1, res["skipped"]
        assert _pending(db) == [], "no draft should be created"
    finally:
        db.close()


def test_categorize_skipped_ambiguous_history():
    """A vendor split 50/50 across two accounts has no dominant account -> skip."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("20")]),
            _purchase(100, 3, "5", [_expense_line("99")], entity_name="Acme Tools"),
        ]
        accounts = _ACCOUNTS + [{"Id": "20", "Name": "Office Supplies"}]
        res = _generate(db, accounts=accounts, purchases=purchases)

        assert res["counts"]["total"] == 0, res["counts"]
        assert res["skipped"].get("no_account_history") == 1, res["skipped"]
    finally:
        db.close()


def test_categorize_ignores_already_categorized():
    """A purchase already on a real account is not a candidate at all."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
            # Already categorized to "10" -> nothing to suggest.
            _purchase(100, 3, "5", [_expense_line("10")], entity_name="Acme Tools"),
        ]
        res = _generate(db, purchases=purchases)

        assert res["counts"]["total"] == 0, res["counts"]
        assert _pending(db) == [], "categorized purchase must be left alone"
    finally:
        db.close()


def test_categorize_dedupe_across_runs():
    """Re-running does not recreate an already-suggested categorize draft."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
            _purchase(100, 3, "5", [_expense_line("99")], entity_name="Acme Tools"),
        ]
        first = _generate(db, purchases=purchases)
        assert first["counts"]["categorize"] == 1, first["counts"]

        second = _generate(db, purchases=purchases)
        assert second["counts"]["total"] == 0, second["counts"]
        assert second["skipped"].get("already_suggested") == 1, second["skipped"]
        assert len(_pending(db)) == 1, "still exactly one draft"
    finally:
        db.close()


# -- create_bill -------------------------------------------------------------

def _receipt(sender, subject, body, date):
    return {"from": sender, "subject": subject, "body": body, "date": date}


def _rfc2822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def test_bill_suggestion_created():
    """A receipt corroborating one QBO vendor + one non-pending bank txn ->
    one create_bill draft with a pure-QBO Bill payload."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="ACME TOOLS PURCHASE", amount=-42.50, txn_date=when)
        db.commit()
        # Vendor 5 history -> dominant account "10".
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        receipts = [_receipt("billing@acmetools.com", "Acme Tools receipt $42.50",
                             "Thanks! Total $42.50", _rfc2822(when))]
        res = _generate(db, purchases=purchases, receipts=receipts)

        assert res["counts"]["create_bill"] == 1, res["counts"]
        rows = [r for r in _pending(db) if r.kind == "create_bill"]
        assert len(rows) == 1, len(rows)
        row = rows[0]
        assert row.target_external_id.startswith("suggestion_bill:"), row.target_external_id
        assert (row.summary or "").startswith("Auto-suggested:"), row.summary

        payload = json.loads(row.payload_json)
        assert payload["VendorRef"]["value"] == "5", payload
        line = payload["Line"][0]
        assert line["Amount"] == 42.5, line
        assert line["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == "10", line
    finally:
        db.close()


def test_bill_skipped_ambiguous_match():
    """Two bank txns with the same amount/vendor in the window -> ambiguous, skip."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="ACME TOOLS", amount=-42.50, txn_date=when)
        _plaid_txn(db, name="ACME TOOLS", amount=-42.50, txn_date=when + timedelta(days=1))
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        receipts = [_receipt("billing@acmetools.com", "Acme Tools receipt $42.50",
                             "Total $42.50", _rfc2822(when))]
        res = _generate(db, purchases=purchases, receipts=receipts)

        assert res["counts"]["create_bill"] == 0, res["counts"]
        assert res["skipped"].get("ambiguous_receipt_match") == 1, res["skipped"]
        assert [r for r in _pending(db) if r.kind == "create_bill"] == []
    finally:
        db.close()


def test_bill_skipped_no_txn_match():
    """A receipt whose amount matches no bank txn is skipped (no fabricated bill)."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="ACME TOOLS", amount=-99.99, txn_date=when)
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        receipts = [_receipt("billing@acmetools.com", "Acme Tools receipt $42.50",
                             "Total $42.50", _rfc2822(when))]
        res = _generate(db, purchases=purchases, receipts=receipts)

        assert res["counts"]["create_bill"] == 0, res["counts"]
        assert res["skipped"].get("no_txn_match") == 1, res["skipped"]
    finally:
        db.close()


def test_bill_no_substring_vendor_false_match():
    """A short vendor name must NOT match on a substring (Pillar 1): vendor "Am"
    must not be fabricated onto an "Amazon" receipt/transaction."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Am")  # pathologically short name
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="AMAZON.COM PURCHASE", amount=-42.50, txn_date=when)
        db.commit()
        # Vendor 5 ("Am") has a confident dominant account -> it WOULD ground a
        # bill if it matched. Token-boundary matching must prevent that.
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        receipts = [_receipt("orders@amazon.com", "Amazon order $42.50",
                             "Total $42.50", _rfc2822(when))]
        res = _generate(db, purchases=purchases, receipts=receipts)

        assert res["counts"]["create_bill"] == 0, res["counts"]
        # "am" appears in no token of the receipt blob -> treated as no vendor.
        assert res["skipped"].get("no_qbo_vendor") == 1, res["skipped"]
        assert [r for r in _pending(db) if r.kind == "create_bill"] == []
    finally:
        db.close()


def test_bill_skipped_email_not_configured():
    """When the receipt reader is unavailable, bills are skipped (audited)."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        res = _generate(db, purchases=purchases, email_available=False)

        assert res["counts"]["create_bill"] == 0, res["counts"]
        assert res["skipped"].get("email_not_configured") == 1, res["skipped"]
    finally:
        db.close()


def test_bill_dedupe_across_runs():
    """Re-running does not recreate an already-suggested bill draft."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="ACME TOOLS", amount=-42.50, txn_date=when)
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
        ]
        receipts = [_receipt("billing@acmetools.com", "Acme Tools receipt $42.50",
                             "Total $42.50", _rfc2822(when))]
        first = _generate(db, purchases=purchases, receipts=receipts)
        assert first["counts"]["create_bill"] == 1, first["counts"]

        second = _generate(db, purchases=purchases, receipts=receipts)
        assert second["counts"]["create_bill"] == 0, second["counts"]
        assert second["skipped"].get("already_suggested") == 1, second["skipped"]
        bills = [r for r in _pending(db) if r.kind == "create_bill"]
        assert len(bills) == 1, "still exactly one bill draft"
    finally:
        db.close()


# -- governance: configuration + no auto-post --------------------------------

def test_qbo_not_configured_returns_actionable():
    """An unconfigured QuickBooks yields configured=False with a message and no
    drafts — not a crash."""
    db = _session()
    try:
        from src.finance.providers import FinanceNotConfigured

        def boom():
            raise FinanceNotConfigured("QuickBooks is not connected.")

        with _patch(attribution, "_email_context", lambda: None), \
                _patch(attribution, "_write_edge", lambda *a, **k: None):
            res = suggestions.generate_suggestions(
                db, qbo_client_factory=boom,
                email_reader_factory=lambda: FakeReader([]))

        assert res["ok"] is False, res
        assert res["configured"] is False, res
        assert res["counts"]["total"] == 0, res
        assert res["message"], "an actionable message is required"
        assert _pending(db) == [], "nothing created when unconfigured"
    finally:
        db.close()


def test_nothing_auto_posts():
    """Every created suggestion (categorize + bill) stays pending_confirm —
    none is executed/posted to QuickBooks."""
    db = _session()
    try:
        _qbo_vendor(db, "5", "Acme Tools")
        when = datetime(2026, 5, 1, 9, 0, 0)
        _plaid_txn(db, name="ACME TOOLS", amount=-42.50, txn_date=when)
        db.commit()
        purchases = [
            _purchase(1, 0, "5", [_expense_line("10")]),
            _purchase(2, 0, "5", [_expense_line("10")]),
            _purchase(100, 3, "5", [_expense_line("99")], entity_name="Acme Tools"),
        ]
        receipts = [_receipt("billing@acmetools.com", "Acme Tools receipt $42.50",
                             "Total $42.50", _rfc2822(when))]
        res = _generate(db, purchases=purchases, receipts=receipts)

        assert res["counts"]["total"] == 2, res["counts"]
        all_rows = db.query(FinanceWriteRequest).all()
        assert len(all_rows) == 2, len(all_rows)
        assert all(r.status == "pending_confirm" for r in all_rows), \
            [r.status for r in all_rows]
        assert all(r.confirmed_at is None for r in all_rows), "nothing confirmed"
    finally:
        db.close()


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as e:
                failures += 1
                print("FAIL %s: %s" % (name, e))
            except Exception as e:  # noqa: BLE001
                failures += 1
                print("ERROR %s: %s" % (name, e))
    if failures:
        print("\n%d test(s) failed." % failures)
        sys.exit(1)
    print("\nAll suggestion tests passed.")


if __name__ == "__main__":
    _run()
