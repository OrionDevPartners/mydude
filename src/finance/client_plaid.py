"""Plaid API client (httpx) — read-only.

Uses the cursor-based ``/transactions/sync`` endpoint so ingest is idempotent and
incremental. The caller persists the ``next_cursor`` between runs. A login-required
or invalid-credential response maps to an actionable error.
"""
import logging

import httpx

from src.finance.providers import (
    plaid_credentials,
    FinanceAuthError,
    FinanceProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_PAGE = 500


class PlaidClient:
    def __init__(self):
        creds = plaid_credentials()
        self._client_id = creds["client_id"]
        self._secret = creds["secret"]
        self._access_token = creds["access_token"]
        self._base = creds["base_url"]
        self.source = creds["source"]

    def _post(self, path, body):
        payload = dict(body or {})
        payload["client_id"] = self._client_id
        payload["secret"] = self._secret
        url = "%s/%s" % (self._base, path.lstrip("/"))
        try:
            resp = httpx.post(url, json=payload, timeout=_TIMEOUT,
                              headers={"Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise FinanceProviderError("Plaid request failed: %s" % e)

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {}
            code = err.get("error_code", "")
            etype = err.get("error_type", "")
            msg = err.get("error_message", resp.text[:300])
            if etype == "INVALID_CREDENTIALS" or code in (
                "INVALID_API_KEYS", "INVALID_ACCESS_TOKEN", "ITEM_LOGIN_REQUIRED",
            ):
                raise FinanceAuthError(
                    "Plaid rejected the request (%s): %s. Reconnect Plaid or "
                    "refresh the access token in the vault." % (code or etype, msg)
                )
            raise FinanceProviderError("Plaid API error (%s): %s" % (code or etype, msg))
        return resp.json()

    def transactions_sync(self, cursor=None):
        """Pull all transaction deltas since ``cursor``.

        Returns ``(added, modified, removed, next_cursor)`` where added/modified
        are transaction dicts and removed is a list of ``{transaction_id}`` dicts.
        Pages until ``has_more`` is false.
        """
        added, modified, removed = [], [], []
        next_cursor = cursor
        # Plaid recommends not sending cursor on the very first request.
        first = True
        while True:
            body = {"count": _PAGE}
            if next_cursor:
                body["cursor"] = next_cursor
            elif not first:
                break
            first = False
            data = self._post("transactions/sync", body)
            added.extend(data.get("added", []) or [])
            modified.extend(data.get("modified", []) or [])
            removed.extend(data.get("removed", []) or [])
            next_cursor = data.get("next_cursor")
            if not data.get("has_more"):
                break
        return added, modified, removed, next_cursor
