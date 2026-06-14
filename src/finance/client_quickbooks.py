"""QuickBooks Online API client (httpx).

Read methods fetch entities; write methods are only ever invoked from the gated
write-back flow (``writeback.confirm_write``). A fresh client is built per call
so connector-proxy token refresh is always honoured. A 401 maps to
``FinanceAuthError`` with an actionable reconnect message.
"""
import json
import logging

import httpx

from src.finance.providers import (
    quickbooks_credentials,
    FinanceAuthError,
    FinanceProviderError,
)

logger = logging.getLogger(__name__)

_MINOR_VERSION = "70"
_TIMEOUT = 20.0


class QuickBooksClient:
    def __init__(self):
        creds = quickbooks_credentials()
        self._token = creds["access_token"]
        self._realm = creds["realm_id"]
        self._base = creds["base_url"]
        self.source = creds["source"]

    # -- low level -------------------------------------------------------- #
    def _headers(self):
        return {
            "Authorization": "Bearer %s" % self._token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _company_url(self, path):
        return "%s/v3/company/%s/%s" % (self._base, self._realm, path.lstrip("/"))

    def _request(self, method, path, params=None, json_body=None):
        params = dict(params or {})
        params.setdefault("minorversion", _MINOR_VERSION)
        url = self._company_url(path)
        try:
            resp = httpx.request(
                method, url, headers=self._headers(),
                params=params, json=json_body, timeout=_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise FinanceProviderError("QuickBooks request failed: %s" % e)

        if resp.status_code in (401, 403):
            raise FinanceAuthError(
                "QuickBooks rejected the credentials (HTTP %s). The access token "
                "likely expired — reconnect QuickBooks in Connected Services or "
                "refresh QUICKBOOKS_ACCESS_TOKEN in the vault." % resp.status_code
            )
        if resp.status_code >= 400:
            raise FinanceProviderError(
                "QuickBooks API error (HTTP %s): %s"
                % (resp.status_code, resp.text[:300])
            )
        try:
            return resp.json()
        except json.JSONDecodeError:
            raise FinanceProviderError("QuickBooks returned a non-JSON response.")

    def query(self, statement):
        """Run a QBO SQL-like query and return the QueryResponse dict."""
        data = self._request("GET", "query", params={"query": statement})
        return (data or {}).get("QueryResponse", {})

    # -- reads ------------------------------------------------------------ #
    def fetch_vendors(self, max_results=200):
        qr = self.query(
            "select Id, DisplayName, Active from Vendor maxresults %d" % max_results
        )
        return qr.get("Vendor", []) or []

    def fetch_accounts(self, max_results=200):
        qr = self.query(
            "select Id, Name, AccountType, Classification from Account "
            "maxresults %d" % max_results
        )
        return qr.get("Account", []) or []

    def fetch_bills(self, max_results=100):
        qr = self.query("select * from Bill maxresults %d" % max_results)
        return qr.get("Bill", []) or []

    def fetch_invoices(self, max_results=100):
        qr = self.query("select * from Invoice maxresults %d" % max_results)
        return qr.get("Invoice", []) or []

    def fetch_purchases(self, max_results=200):
        """Fetch Purchases (expenses / checks / credit-card charges) including
        their expense lines.

        Used by the suggestion engine to detect uncategorised purchases and to
        learn each vendor's dominant expense account from history. ``select *``
        returns ``SyncToken`` and the full ``Line`` array needed to build a
        sparse re-categorisation update.
        """
        qr = self.query("select * from Purchase maxresults %d" % max_results)
        return qr.get("Purchase", []) or []

    # -- writes (gated only) --------------------------------------------- #
    def create_bill(self, payload):
        """Create a Bill. ``payload`` must be a valid QBO Bill object."""
        return self._request("POST", "bill", json_body=payload)

    def create_invoice(self, payload):
        """Create an Invoice. ``payload`` must be a valid QBO Invoice object."""
        return self._request("POST", "invoice", json_body=payload)

    def update_purchase(self, payload):
        """Sparse-update a Purchase (e.g. re-categorise). ``payload`` must include
        Id, SyncToken and ``sparse: true``."""
        return self._request("POST", "purchase", json_body=payload)
