"""Plaid API client (httpx).

Read paths use the cursor-based ``/transactions/sync`` endpoint so ingest is
idempotent and incremental (the caller persists ``next_cursor`` per Item). The
Plaid Link onboarding flow is handled by ``create_link_token`` +
``exchange_public_token``; ``item_remove`` revokes an Item at Plaid. A
login-required or invalid-credential response maps to an actionable error.

App-level credentials (client_id + secret) come from ``plaid_app_credentials``.
A per-Item ``access_token`` is supplied by the caller for item-scoped calls
(transactions sync, item remove); it is never required for link/exchange.
"""
import logging

import httpx

from src.finance.providers import (
    plaid_app_credentials,
    FinanceAuthError,
    FinanceProviderError,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_PAGE = 500


class PlaidClient:
    def __init__(self, access_token=None, app_creds=None):
        if app_creds is None:
            app_creds = plaid_app_credentials()
        self._client_id = app_creds["client_id"]
        self._secret = app_creds["secret"]
        self._base = app_creds["base_url"]
        self.source = app_creds.get("source")
        self._access_token = access_token

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
                    "Plaid rejected the request (%s): %s. Reconnect the bank via "
                    "Plaid Link." % (code or etype, msg)
                )
            raise FinanceProviderError("Plaid API error (%s): %s" % (code or etype, msg))
        return resp.json()

    # -- Link onboarding (app-level; no access token required) -------------- #

    def create_link_token(self, user_id, products=None, country_codes=None,
                          language=None, client_name=None, redirect_uri=None,
                          webhook=None):
        """Create a short-lived ``link_token`` to initialize Plaid Link client-side.

        Returns ``{link_token, expiration, request_id}``. The link_token is meant
        for the browser and is safe to return to an authenticated operator."""
        body = {
            "user": {"client_user_id": str(user_id)},
            "client_name": client_name or "MyDude.io",
            "products": list(products) if products else ["transactions"],
            "country_codes": list(country_codes) if country_codes else ["US"],
            "language": language or "en",
        }
        if redirect_uri:
            body["redirect_uri"] = redirect_uri
        if webhook:
            body["webhook"] = webhook
        data = self._post("link/token/create", body)
        link_token = data.get("link_token")
        if not link_token:
            raise FinanceProviderError("Plaid did not return a link_token.")
        return {"link_token": link_token, "expiration": data.get("expiration"),
                "request_id": data.get("request_id")}

    def exchange_public_token(self, public_token):
        """Exchange a Link ``public_token`` for a long-lived ``access_token``.

        Returns ``{access_token, item_id, request_id}``. The caller must encrypt
        and store the access_token (per Item) — it must never reach the client."""
        if not public_token:
            raise FinanceProviderError(
                "A Plaid public_token is required to exchange for an access token.")
        data = self._post("item/public_token/exchange", {"public_token": public_token})
        access_token = data.get("access_token")
        item_id = data.get("item_id")
        if not access_token or not item_id:
            raise FinanceProviderError(
                "Plaid exchange did not return an access token / item id.")
        return {"access_token": access_token, "item_id": item_id,
                "request_id": data.get("request_id")}

    # -- Item-scoped calls (require an access token) ------------------------ #

    def item_remove(self):
        """Revoke this Item at Plaid (invalidates its access token)."""
        if not self._access_token:
            raise FinanceProviderError(
                "Cannot remove a Plaid item without its access token.")
        data = self._post("item/remove", {"access_token": self._access_token})
        return {"removed": True, "request_id": data.get("request_id")}

    def transactions_sync(self, cursor=None):
        """Pull all transaction deltas since ``cursor``.

        Returns ``(added, modified, removed, next_cursor)`` where added/modified
        are transaction dicts and removed is a list of ``{transaction_id}`` dicts.
        Pages until ``has_more`` is false.
        """
        if not self._access_token:
            raise FinanceProviderError(
                "Cannot sync Plaid transactions without an Item access token. "
                "Connect a bank via Plaid Link first.")
        added, modified, removed = [], [], []
        next_cursor = cursor
        # Plaid recommends not sending cursor on the very first request.
        first = True
        while True:
            body = {"access_token": self._access_token, "count": _PAGE}
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
