"""Unit tests for Plaid ingest + cursor handling (``src/finance/sync.py``).

These pin the correctness guarantees of read-only Plaid ingest:
  * upsert on ``(source, external_id)`` — re-syncing the same transaction updates
    it in place and never creates a duplicate;
  * an already-attributed transaction is NOT re-queued for attribution on
    re-sync (its project attribution is preserved);
  * pending -> posted supersede — a posted transaction removes the earlier
    pending row it replaces (via the ``pending_transaction_id`` link), and
    Plaid's ``removed`` list deletes rows;
  * the per-Item sync cursor advances and the next sync resumes from it.

Hermetic: a fresh in-memory SQLite DB per test, a fake ``PlaidClient``, and
stubbed app credentials — no network, no live Plaid creds, no shared-DB state.

Runnable two ways:
  * ``python tests/test_finance_sync_ingest.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_finance_sync_ingest.py``   (test_* functions; no plugins)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src import models  # noqa: F401  (registers all tables on Base.metadata)
from src.models import FinanceTransaction, PlaidItem
from src.finance import sync as fsync
from src.finance import client_plaid as cp
from src.finance import providers as fp
from src.finance.sync import _ingest_plaid_deltas


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


@contextmanager
def _env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _ptxn(ext, amount=10.0, name="Acme", pending=False, pending_id=None,
          date="2026-06-01"):
    """A minimal Plaid /transactions/sync transaction dict."""
    return {
        "transaction_id": ext,
        "name": name,
        "merchant_name": name,
        "amount": amount,
        "date": date,
        "account_id": "acct-1",
        "iso_currency_code": "USD",
        "pending": pending,
        "pending_transaction_id": pending_id,
        "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"},
    }


def _count(db, ext):
    return db.query(FinanceTransaction).filter(
        FinanceTransaction.source == "plaid",
        FinanceTransaction.external_id == ext).count()


# -- upsert: no duplicates on re-sync ----------------------------------------

def test_upsert_idempotent_no_duplicate_on_resync():
    db = _session()
    try:
        ids = []
        _ingest_plaid_deltas(db, [_ptxn("u1", amount=12.5, pending=True)], [], [], ids)
        db.commit()
        assert _count(db, "u1") == 1, "first ingest should create exactly one row"
        assert len(ids) == 1, ids
        row_id = ids[0]

        # Re-sync the SAME transaction as a modification (corrected + posted).
        ids2 = []
        _ingest_plaid_deltas(db, [], [_ptxn("u1", amount=13.0, pending=False)], [], ids2)
        db.commit()

        assert _count(db, "u1") == 1, "re-sync must not create a duplicate"
        row = db.query(FinanceTransaction).filter(
            FinanceTransaction.external_id == "u1").one()
        assert row.id == row_id, "upsert must update the same row, not replace it"
        assert abs(row.amount - 13.0) < 1e-9, row.amount
        assert row.pending is False, row.pending
    finally:
        db.close()


def test_resync_preserves_existing_attribution():
    db = _session()
    try:
        ids = []
        _ingest_plaid_deltas(db, [_ptxn("a1")], [], [], ids)
        db.commit()
        row = db.query(FinanceTransaction).filter(
            FinanceTransaction.external_id == "a1").one()
        # Simulate attribution having run and pinned a project.
        row.attribution_status = "attributed"
        row.attribution_method = "rule"
        row.project_id = 4242
        db.commit()

        ids2 = []
        _ingest_plaid_deltas(db, [], [_ptxn("a1", amount=99.0)], [], ids2)
        db.commit()
        db.refresh(row)
        assert ids2 == [], "an attributed row must not be re-queued for attribution"
        assert row.project_id == 4242, "re-sync must preserve existing attribution"
        assert abs(row.amount - 99.0) < 1e-9, "other fields still update on re-sync"
    finally:
        db.close()


# -- pending -> posted supersede ---------------------------------------------

def test_pending_superseded_by_posted_via_link():
    db = _session()
    try:
        # Sync 1: a pending charge.
        _ingest_plaid_deltas(db, [_ptxn("pend-1", amount=20.0, pending=True)], [], [], [])
        db.commit()
        assert _count(db, "pend-1") == 1

        # Sync 2: the posted version links back to the pending id.
        _ingest_plaid_deltas(
            db, [_ptxn("post-1", amount=20.0, pending=False, pending_id="pend-1")],
            [], [], [])
        db.commit()

        assert _count(db, "pend-1") == 0, "pending row must be superseded"
        assert _count(db, "post-1") == 1
        posted = db.query(FinanceTransaction).filter(
            FinanceTransaction.external_id == "post-1").one()
        assert posted.pending is False, posted.pending
    finally:
        db.close()


def test_removed_list_deletes_rows():
    db = _session()
    try:
        _ingest_plaid_deltas(db, [_ptxn("rm-1"), _ptxn("rm-2")], [], [], [])
        db.commit()
        assert _count(db, "rm-1") == 1 and _count(db, "rm-2") == 1

        # Plaid sends removed ids as dicts; the ingest also tolerates bare strings.
        removed_count = _ingest_plaid_deltas(
            db, [], [], [{"transaction_id": "rm-1"}, "rm-2"], [])
        db.commit()
        assert removed_count == 2, removed_count
        assert _count(db, "rm-1") == 0 and _count(db, "rm-2") == 0
    finally:
        db.close()


# -- cursor advancement + resumption through sync_plaid ----------------------

_FAKE_APP = {"client_id": "c", "secret": "s", "base_url": "https://x.test",
             "env": "sandbox", "source": "vault"}


class _FakeClient:
    """Replays scripted (added, modified, removed, next_cursor) results and
    records the cursor it was called with, in order."""
    scripts = []   # popped once per transactions_sync call
    calls = []     # cursors seen, in order

    def __init__(self, access_token=None, app_creds=None):
        self._tok = access_token

    def transactions_sync(self, cursor=None):
        _FakeClient.calls.append(cursor)
        return _FakeClient.scripts.pop(0)


@contextmanager
def _sync_patches():
    with _patch(cp, "PlaidClient", _FakeClient), \
            _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP)), \
            _patch(fp, "get_connection_settings", lambda name: None), \
            _env(REPLIT_DEPLOYMENT=None, PLAID_ACCESS_TOKEN=None):
        yield


def test_cursor_advances_and_next_sync_resumes():
    db = _session()
    try:
        with _env(REPLIT_DEPLOYMENT=None):
            fp.save_plaid_item(db, item_id="cur-item", access_token="tok-cur",
                               institution_name="Bank C", source="link")

        # First sync: starts with no cursor, advances to "cursor-A".
        _FakeClient.scripts = [([], [], [], "cursor-A")]
        _FakeClient.calls = []
        with _sync_patches():
            r1 = fsync.sync_plaid(db, trigger="manual")
        assert r1.get("ok") is True, r1
        assert _FakeClient.calls == [None], _FakeClient.calls
        item = db.query(PlaidItem).filter(PlaidItem.item_id == "cur-item").one()
        assert item.cursor == "cursor-A", item.cursor
        assert item.status == "active", item.status
        assert item.last_synced_at is not None, "a successful sync records last_synced_at"

        # Second sync: must resume from the stored cursor and advance again.
        _FakeClient.scripts = [([], [], [], "cursor-B")]
        _FakeClient.calls = []
        with _sync_patches():
            r2 = fsync.sync_plaid(db, trigger="scheduled")
        assert r2.get("ok") is True, r2
        assert _FakeClient.calls == ["cursor-A"], _FakeClient.calls
        db.refresh(item)
        assert item.cursor == "cursor-B", item.cursor
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
    print("\nAll sync ingest tests passed.")


if __name__ == "__main__":
    _run()
