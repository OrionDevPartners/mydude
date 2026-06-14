"""Tests for the Plaid Link onboarding + multi-Item transactions sync.

These run offline, with no real Plaid credentials or network. ``httpx.post`` is
faked at the ``client_plaid`` seam, so the suite is hermetic. The DB-backed
tests use the app's real database but create + delete their own rows.

Covered:
  1. Client: every ``/transactions/sync`` request body carries the Plaid auth
     pair (``client_id`` + ``secret``, injected by the client) plus the per-Item
     ``access_token`` and ``count``; the cursor protocol is honoured and paging
     accumulates added/modified/removed until ``has_more`` is false.
  2. Client: ``create_link_token`` / ``exchange_public_token`` / ``item_remove``
     post the right bodies to the right endpoints and return the expected fields.
  3. Client: ``transactions_sync`` and ``item_remove`` fail loud (no silent
     fallback) when no Item access token is set.
  4. Providers: ``plaid_app_credentials`` fails loud when client_id/secret are
     absent — no bank can be linked against missing credentials.
  5. Providers: ``list_plaid_items`` NEVER leaks an access token (encrypted or
     plaintext) — only masked summaries.
  6. Sync: multiple Items each sync with their OWN cursor; a per-Item auth
     failure marks that Item ``error`` and does not abort the others; cursors +
     last_synced_at persist on the surviving rows.
  7. API: the JSON endpoints require auth, and the link-token endpoint never
     returns an access token to the browser.

Runnable two ways:
  * ``python tests/test_finance_plaid_sync.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_finance_plaid_sync.py``   (test_* functions; no plugins needed)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.finance import client_plaid as cp
from src.finance import providers as fp
from src.finance.providers import (
    FinanceAuthError, FinanceNotConfigured, FinanceProviderError,
)


# -- fakes -------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


# APP-level credentials only — NO access token (per-Item tokens live elsewhere).
_FAKE_APP_CREDS = {
    "client_id": "cid-test",
    "secret": "sec-test",
    "base_url": "https://sandbox.plaid.test",
    "env": "sandbox",
    "source": "vault",
}


@contextmanager
def _fake_post(pages):
    """Patch ``client_plaid.httpx.post`` to replay ``pages`` + record request bodies."""
    captured = []
    state = {"i": 0}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.append({"url": url, "body": dict(json or {})})
        page = pages[state["i"]]
        state["i"] += 1
        return _FakeResp(page)

    orig = cp.httpx.post
    cp.httpx.post = fake_post  # type: ignore[assignment]
    try:
        yield captured
    finally:
        cp.httpx.post = orig  # type: ignore[assignment]


@contextmanager
def _patch(obj, name, value):
    """Temporarily set ``obj.name = value``, restoring the original afterwards."""
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


def _client(access_token="access-test-token"):
    """A PlaidClient with fake app creds (no network during construction)."""
    return cp.PlaidClient(access_token=access_token, app_creds=dict(_FAKE_APP_CREDS))


# -- 1. transactions sync: auth + cursor + paging ---------------------------

def test_sync_includes_auth_and_pages():
    pages = [
        {"added": [{"transaction_id": "t1", "amount": 10.0}],
         "modified": [], "removed": [],
         "next_cursor": "cursor-1", "has_more": True},
        {"added": [{"transaction_id": "t2", "amount": 20.0}],
         "modified": [{"transaction_id": "t1", "amount": 11.0}],
         "removed": [{"transaction_id": "t0"}],
         "next_cursor": "cursor-2", "has_more": False},
    ]
    with _fake_post(pages) as captured:
        client = _client()
        added, modified, removed, next_cursor = client.transactions_sync()

    # Every request carries the Plaid auth pair + access token + count.
    assert len(captured) == 2, "expected two paged requests, got %d" % len(captured)
    for req in captured:
        body = req["body"]
        assert body.get("client_id") == "cid-test", body
        assert body.get("secret") == "sec-test", body
        assert body.get("access_token") == "access-test-token", body
        assert body.get("count"), body
        assert req["url"].endswith("/transactions/sync"), req["url"]

    # Cursor protocol: none on first call, server cursor on the second.
    assert "cursor" not in captured[0]["body"], captured[0]["body"]
    assert captured[1]["body"].get("cursor") == "cursor-1", captured[1]["body"]

    # Paging accumulates deltas and returns the final cursor.
    assert [t["transaction_id"] for t in added] == ["t1", "t2"], added
    assert [t["transaction_id"] for t in modified] == ["t1"], modified
    assert removed == [{"transaction_id": "t0"}], removed
    assert next_cursor == "cursor-2", next_cursor


def test_sync_resumes_from_existing_cursor():
    pages = [
        {"added": [], "modified": [], "removed": [],
         "next_cursor": "cursor-9", "has_more": False},
    ]
    with _fake_post(pages) as captured:
        client = _client()
        client.transactions_sync(cursor="cursor-8")

    assert len(captured) == 1, captured
    body = captured[0]["body"]
    # When resuming, the stored cursor goes out on the very first request.
    assert body.get("cursor") == "cursor-8", body
    assert body.get("access_token") == "access-test-token", body


# -- 2. Link onboarding: link-token / exchange / item-remove ----------------

def test_create_link_token_body_and_result():
    payload = {"link_token": "link-sandbox-abc", "expiration": "2026-01-01T00:00:00Z",
               "request_id": "req-1"}
    with _fake_post([payload]) as captured:
        client = cp.PlaidClient(app_creds=dict(_FAKE_APP_CREDS))  # no access token needed
        out = client.create_link_token(user_id="mydude-operator",
                                       products=["transactions"], country_codes=["US"])

    assert out["link_token"] == "link-sandbox-abc", out
    assert len(captured) == 1, captured
    req = captured[0]
    assert req["url"].endswith("/link/token/create"), req["url"]
    body = req["body"]
    assert body["client_id"] == "cid-test" and body["secret"] == "sec-test", body
    assert body["user"]["client_user_id"] == "mydude-operator", body
    assert body["products"] == ["transactions"], body
    assert body["country_codes"] == ["US"], body
    # A link_token is NOT an access token — it is browser-safe by design.
    assert "access_token" not in out, out


def test_exchange_public_token_body_and_result():
    payload = {"access_token": "access-NEW", "item_id": "item-123", "request_id": "req-2"}
    with _fake_post([payload]) as captured:
        client = cp.PlaidClient(app_creds=dict(_FAKE_APP_CREDS))
        out = client.exchange_public_token("public-sandbox-xyz")

    assert out == {"access_token": "access-NEW", "item_id": "item-123", "request_id": "req-2"}
    req = captured[0]
    assert req["url"].endswith("/item/public_token/exchange"), req["url"]
    body = req["body"]
    assert body["public_token"] == "public-sandbox-xyz", body
    assert body["client_id"] == "cid-test" and body["secret"] == "sec-test", body


def test_item_remove_posts_access_token():
    with _fake_post([{"request_id": "req-3"}]) as captured:
        client = _client(access_token="access-to-revoke")
        out = client.item_remove()

    assert out["removed"] is True, out
    req = captured[0]
    assert req["url"].endswith("/item/remove"), req["url"]
    assert req["body"].get("access_token") == "access-to-revoke", req["body"]


# -- 3. fail loud without an Item access token ------------------------------

def test_sync_fails_loud_without_access_token():
    client = cp.PlaidClient(access_token=None, app_creds=dict(_FAKE_APP_CREDS))
    try:
        client.transactions_sync()
    except FinanceProviderError:
        pass
    else:
        raise AssertionError("transactions_sync must fail loud without an access token")


def test_item_remove_fails_loud_without_access_token():
    client = cp.PlaidClient(access_token=None, app_creds=dict(_FAKE_APP_CREDS))
    try:
        client.item_remove()
    except FinanceProviderError:
        pass
    else:
        raise AssertionError("item_remove must fail loud without an access token")


# -- 4. providers: app credentials fail loud when unconfigured --------------

def test_plaid_app_credentials_fail_loud_when_unconfigured():
    # No connector settings and no env credentials -> must raise, never mock.
    with _patch(fp, "get_connection_settings", lambda name: None), \
            _env(PLAID_CLIENT_ID=None, PLAID_SECRET=None):
        try:
            fp.plaid_app_credentials()
        except FinanceNotConfigured:
            pass
        else:
            raise AssertionError("plaid_app_credentials must fail loud when unconfigured")


# -- 5 & 6. DB-backed: no token leak + multi-Item cursor persistence --------

class _SyncFake:
    """Per-Item fake PlaidClient keyed by access token (set up per test)."""
    behaviors = {}   # access_token -> ("ok", added, modified, removed, next_cursor) | ("auth", msg)
    seen = []        # (access_token, cursor) in call order

    def __init__(self, access_token=None, app_creds=None):
        self._tok = access_token

    def transactions_sync(self, cursor=None):
        _SyncFake.seen.append((self._tok, cursor))
        b = _SyncFake.behaviors[self._tok]
        if b[0] == "auth":
            raise FinanceAuthError(b[1])
        return b[1], b[2], b[3], b[4]


def test_list_plaid_items_never_leaks_token():
    from src.database import SessionLocal, init_db
    init_db()  # ensure the plaid_items table exists (create_all + column sync)
    db = SessionLocal()
    created = []
    try:
        row = fp.save_plaid_item(db, item_id="tok-leak-item", access_token="access-secret-AAA",
                                 institution_name="Test CU", source="link")
        created.append(row.id)
        items = fp.list_plaid_items(db)
        mine = [i for i in items if i["item_id"] == "tok-leak-item"]
        assert mine, "expected the seeded item to appear in list_plaid_items"
        for i in items:
            assert "access_token" not in i, i
            assert "encrypted_access_token" not in i, i
            for v in i.values():
                assert v != "access-secret-AAA", "raw access token leaked: %r" % i
    finally:
        from src.models import PlaidItem
        db.rollback()  # clear any aborted txn before cleanup
        for pk in created:
            db.query(PlaidItem).filter(PlaidItem.id == pk).delete()
        db.commit()
        db.close()


def test_sync_multi_item_per_cursor_and_isolated_failure():
    from sqlalchemy import func
    from src.database import SessionLocal
    from src.models import PlaidItem, FinanceSyncRun
    from src.finance import sync as fsync

    from src.database import init_db
    init_db()  # ensure plaid_items / finance_sync_runs exist
    db = SessionLocal()
    created = []
    pre_run_id = 0
    try:
        pre_run_id = db.query(func.max(FinanceSyncRun.id)).scalar() or 0

        a = fp.save_plaid_item(db, item_id="multi-A", access_token="tok-A",
                               institution_name="Bank A", source="link")
        b = fp.save_plaid_item(db, item_id="multi-B", access_token="tok-B",
                               institution_name="Bank B", source="link")
        created += [a.id, b.id]
        # Give Item A an existing cursor so we can prove it resumes from its own.
        a.cursor = "A-old"
        a.status = "active"
        db.commit()

        # A succeeds with no deltas (keeps the DB clean) and advances its cursor;
        # B fails auth and must be isolated.
        _SyncFake.behaviors = {
            "tok-A": ("ok", [], [], [], "A-new"),
            "tok-B": ("auth", "Plaid login required for Bank B"),
        }
        _SyncFake.seen = []

        with _patch(cp, "PlaidClient", _SyncFake), \
                _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP_CREDS)):
            report = fsync.sync_plaid(db, trigger="manual")

        # Run succeeds overall because at least one Item synced.
        assert report.get("ok") is True, report

        # Each Item was synced with ITS OWN cursor (A resumed from "A-old").
        seen = dict(_SyncFake.seen)
        assert seen.get("tok-A") == "A-old", _SyncFake.seen
        assert "tok-B" in seen, _SyncFake.seen

        db.refresh(a)
        db.refresh(b)
        # A persisted its advanced cursor + healthy status + sync timestamp.
        assert a.cursor == "A-new", a.cursor
        assert a.status == "active", a.status
        assert a.last_error is None, a.last_error
        assert a.last_synced_at is not None, "A should record last_synced_at"
        # B's failure was isolated to its own row (did not abort A).
        assert b.status == "error", b.status
        assert b.last_error and "Bank B" in b.last_error, b.last_error
    finally:
        db.rollback()  # clear any aborted txn before cleanup
        for pk in created:
            db.query(PlaidItem).filter(PlaidItem.id == pk).delete()
        db.query(FinanceSyncRun).filter(FinanceSyncRun.source == "plaid",
                                        FinanceSyncRun.id > pre_run_id).delete()
        db.commit()
        db.close()


# -- 7. API endpoints: auth required + no token returned --------------------

def _api_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.api.router import router as api_router
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app, raise_server_exceptions=False)


def test_plaid_endpoints_require_auth():
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
        client = _api_client()
        r = client.get("/api/finance/plaid/items", follow_redirects=False)
        # require_auth raises a 303 redirect to /login for unauthenticated callers.
        assert r.status_code in (303, 401), r.status_code


def test_link_token_endpoint_never_returns_access_token():
    class _LinkFake:
        def __init__(self, access_token=None, app_creds=None):
            pass

        def create_link_token(self, user_id, products=None, country_codes=None,
                              redirect_uri=None, **kw):
            return {"link_token": "link-sandbox-zzz", "expiration": None,
                    "request_id": "req-x"}

    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), \
            _patch(cp, "PlaidClient", _LinkFake), \
            _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP_CREDS)):
        client = _api_client()
        r = client.post("/api/finance/plaid/link-token")
        assert r.status_code == 200, (r.status_code, r.text)
        data = r.json()
        assert data.get("link_token") == "link-sandbox-zzz", data
        assert "access_token" not in r.text, r.text


def test_link_token_endpoint_audits_success():
    from sqlalchemy import func
    from src.database import SessionLocal, init_db
    from src.models import FinanceAuditLog

    class _LinkFake:
        def __init__(self, access_token=None, app_creds=None):
            pass

        def create_link_token(self, user_id, products=None, country_codes=None,
                              redirect_uri=None, **kw):
            return {"link_token": "link-sandbox-aud", "expiration": None}

    init_db()
    db = SessionLocal()
    pre_id = db.query(func.max(FinanceAuditLog.id)).scalar() or 0
    db.close()
    try:
        with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), \
                _patch(cp, "PlaidClient", _LinkFake), \
                _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP_CREDS)):
            client = _api_client()
            assert client.post("/api/finance/plaid/link-token").status_code == 200

        db = SessionLocal()
        rows = db.query(FinanceAuditLog).filter(
            FinanceAuditLog.id > pre_id,
            FinanceAuditLog.action == "plaid_link_token").all()
        assert any(r.status == "ok" for r in rows), "link-token success must be audited"
        for r in rows:  # audit detail must never carry the link token
            assert not r.detail or "link-sandbox" not in r.detail, r.detail
        db.close()
    finally:
        db = SessionLocal()
        db.query(FinanceAuditLog).filter(FinanceAuditLog.id > pre_id).delete()
        db.commit()
        db.close()


def test_exchange_failure_is_audited():
    from sqlalchemy import func
    from src.database import SessionLocal, init_db
    from src.models import FinanceAuditLog

    class _BoomFake:
        def __init__(self, access_token=None, app_creds=None):
            pass

        def exchange_public_token(self, public_token):
            raise FinanceProviderError("Plaid API error (RATE_LIMIT): slow down")

    init_db()
    db = SessionLocal()
    pre_id = db.query(func.max(FinanceAuditLog.id)).scalar() or 0
    db.close()
    try:
        with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), \
                _patch(cp, "PlaidClient", _BoomFake), \
                _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP_CREDS)):
            client = _api_client()
            r = client.post("/api/finance/plaid/exchange",
                            data={"public_token": "public-fail"})
            assert r.status_code == 502, (r.status_code, r.text)

        db = SessionLocal()
        rows = db.query(FinanceAuditLog).filter(
            FinanceAuditLog.id > pre_id,
            FinanceAuditLog.action == "plaid_item_connected").all()
        assert any(r.status == "error" for r in rows), "exchange failure must be audited"
        db.close()
    finally:
        db = SessionLocal()
        db.query(FinanceAuditLog).filter(FinanceAuditLog.id > pre_id).delete()
        db.commit()
        db.close()


def test_exchange_endpoint_never_returns_access_token():
    from sqlalchemy import func
    from src.database import SessionLocal, init_db
    from src.models import PlaidItem, FinanceAuditLog

    class _ExchangeFake:
        def __init__(self, access_token=None, app_creds=None):
            pass

        def exchange_public_token(self, public_token):
            return {"access_token": "access-MUST-NOT-LEAK", "item_id": "api-exch-item",
                    "request_id": "r"}

    init_db()
    db = SessionLocal()
    pre_audit = db.query(func.max(FinanceAuditLog.id)).scalar() or 0
    db.close()
    try:
        with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None), \
                _patch(cp, "PlaidClient", _ExchangeFake), \
                _patch(fp, "plaid_app_credentials", lambda: dict(_FAKE_APP_CREDS)):
            client = _api_client()
            r = client.post("/api/finance/plaid/exchange",
                            data={"public_token": "public-ok",
                                  "institution_name": "Acme Bank"})
            assert r.status_code == 200, (r.status_code, r.text)
            assert "access-MUST-NOT-LEAK" not in r.text, r.text
            assert "access_token" not in r.text, r.text
            assert r.json().get("item_id") == "api-exch-item", r.json()
    finally:
        db = SessionLocal()
        db.rollback()
        db.query(PlaidItem).filter(PlaidItem.item_id == "api-exch-item").delete()
        db.query(FinanceAuditLog).filter(FinanceAuditLog.id > pre_audit).delete()
        db.commit()
        db.close()


def test_save_plaid_item_prod_encryption_guard():
    from src.database import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        # In production, an ephemeral ENCRYPTION_KEY must refuse to store the token
        # (it would be undecryptable after restart) — fail loud, persist nothing.
        with _env(REPLIT_DEPLOYMENT="1"), \
                _patch(fp, "encryption_key_is_persistent", lambda: False):
            try:
                fp.save_plaid_item(db, item_id="guard-item", access_token="tok-guard",
                                   source="link")
            except FinanceProviderError:
                pass
            else:
                raise AssertionError(
                    "save_plaid_item must refuse an ephemeral key in production")
        db.rollback()
        from src.models import PlaidItem
        leaked = db.query(PlaidItem).filter(PlaidItem.item_id == "guard-item").first()
        assert leaked is None, "guard must not persist a token row"
    finally:
        from src.models import PlaidItem
        db.rollback()
        db.query(PlaidItem).filter(PlaidItem.item_id == "guard-item").delete()
        db.commit()
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
    print("\nAll Plaid sync tests passed.")


if __name__ == "__main__":
    _run()
