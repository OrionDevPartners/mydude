"""Tests for the Plaid transactions/sync client composition and paging.

These run offline, with no real Plaid credentials or network:

  1. Every outbound ``/transactions/sync`` request body includes the three
     fields Plaid requires for an authenticated item read — ``client_id``,
     ``secret``, and ``access_token`` — plus ``count``. (Regression guard: the
     ``access_token`` was previously omitted, so ingest always failed.)
  2. The cursor protocol is honoured: no ``cursor`` on the first request, then
     the server-issued ``next_cursor`` on each subsequent request.
  3. Paging accumulates ``added``/``modified``/``removed`` across pages until
     ``has_more`` is false, and returns the final ``next_cursor``.

``httpx.post`` and ``plaid_credentials`` are faked, so the suite is hermetic.

Runnable two ways:
  * ``python tests/test_finance_plaid_sync.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_finance_plaid_sync.py``   (test_* functions; no plugins needed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.finance import client_plaid as cp


# -- fakes -------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


_FAKE_CREDS = {
    "client_id": "cid-test",
    "secret": "sec-test",
    "access_token": "access-test-token",
    "base_url": "https://sandbox.plaid.test",
    "source": "vault",
}


def _make_client(pages, captured):
    """Build a PlaidClient whose httpx.post replays ``pages`` and records bodies."""
    state = {"i": 0}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.append({"url": url, "body": dict(json or {})})
        page = pages[state["i"]]
        state["i"] += 1
        return _FakeResp(page)

    cp.plaid_credentials = lambda: dict(_FAKE_CREDS)  # type: ignore[assignment]
    cp.httpx.post = fake_post  # type: ignore[assignment]
    return cp.PlaidClient()


# -- tests -------------------------------------------------------------------

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
    captured = []
    client = _make_client(pages, captured)

    added, modified, removed, next_cursor = client.transactions_sync()

    # 1. Every request carries the required Plaid auth triple + count.
    assert len(captured) == 2, "expected two paged requests, got %d" % len(captured)
    for req in captured:
        body = req["body"]
        assert body.get("client_id") == "cid-test", body
        assert body.get("secret") == "sec-test", body
        assert body.get("access_token") == "access-test-token", body
        assert body.get("count"), body
        assert req["url"].endswith("/transactions/sync"), req["url"]

    # 2. Cursor protocol: none on first call, server cursor on the second.
    assert "cursor" not in captured[0]["body"], captured[0]["body"]
    assert captured[1]["body"].get("cursor") == "cursor-1", captured[1]["body"]

    # 3. Paging accumulates deltas and returns the final cursor.
    assert [t["transaction_id"] for t in added] == ["t1", "t2"], added
    assert [t["transaction_id"] for t in modified] == ["t1"], modified
    assert removed == [{"transaction_id": "t0"}], removed
    assert next_cursor == "cursor-2", next_cursor


def test_sync_resumes_from_existing_cursor():
    pages = [
        {"added": [], "modified": [], "removed": [],
         "next_cursor": "cursor-9", "has_more": False},
    ]
    captured = []
    client = _make_client(pages, captured)

    client.transactions_sync(cursor="cursor-8")

    assert len(captured) == 1, captured
    body = captured[0]["body"]
    # When resuming, the stored cursor goes out on the very first request.
    assert body.get("cursor") == "cursor-8", body
    assert body.get("access_token") == "access-test-token", body


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
