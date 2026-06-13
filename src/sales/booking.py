"""Calendly meeting-booking provider for the sales subsystem.

Provider-agnostic at the call site: the conversation engine only ever asks the
capability broker for ``calendly_book`` and receives a booking URL — it never
imports this module directly. Swapping Calendly for another scheduler means
adding a sibling provider, not touching the conversation flow.

Credential sourcing (mirrors src/finance/providers.py):
  1. Replit connector proxy (fetched fresh every call, never cached/persisted)
  2. Environment variable fallback (CALENDLY_API_TOKEN / CALENDLY_ACCESS_TOKEN)
  3. Neither configured -> SalesNotConfigured (fail loud, NO mock booking link)

Status functions never raise — they report honestly so the UI can tell the
operator exactly what to connect. Credential/booking functions raise loudly.
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

import httpx

from src.web.connectors import get_connection_settings

logger = logging.getLogger(__name__)

_CALENDLY_BASE = "https://api.calendly.com"
_TIMEOUT = 20.0


class SalesNotConfigured(RuntimeError):
    """Raised when the booking provider has no usable credentials."""


class SalesAuthError(RuntimeError):
    """Raised when the provider rejects our credentials (expired / invalid)."""


class SalesProviderError(RuntimeError):
    """Raised on a non-auth provider/API failure."""


def _env(*names: str) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return None


def calendly_credentials() -> Dict[str, str]:
    """Return ``{access_token, source}`` or raise SalesNotConfigured.

    Connector proxy first (refreshes OAuth tokens automatically), then the
    environment fallback. The raw token is never logged or persisted.
    """
    settings = get_connection_settings("calendly")
    if settings:
        token = (
            settings.get("access_token")
            or settings.get("accessToken")
            or ((settings.get("oauth") or {}).get("credentials") or {}).get("access_token")
        )
        if token:
            return {"access_token": token, "source": "connector"}

    token = _env("CALENDLY_API_TOKEN", "CALENDLY_ACCESS_TOKEN", "CALENDLY_PAT")
    if token:
        return {"access_token": token, "source": "env"}

    raise SalesNotConfigured(
        "Calendly is not connected. Connect the Calendly integration, or set a "
        "CALENDLY_API_TOKEN secret (a Calendly personal access token), to let "
        "qualified prospects book meetings."
    )


def calendly_status() -> Dict[str, Any]:
    """Non-raising connection report for the UI.

    Returns ``{connected, source, detail}``. Does a lightweight live identity
    check when credentials are present so the operator sees real status, not a
    guess. Never raises and never echoes the token.
    """
    try:
        creds = calendly_credentials()
    except SalesNotConfigured as e:
        return {"connected": False, "source": None, "detail": str(e)}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                _CALENDLY_BASE + "/users/me",
                headers={"Authorization": "Bearer " + creds["access_token"]},
            )
        if resp.status_code == 401:
            return {"connected": False, "source": creds["source"],
                    "detail": "Calendly rejected the token (expired or invalid). Reconnect Calendly."}
        resp.raise_for_status()
        name = (((resp.json() or {}).get("resource") or {}).get("name")) or "Calendly account"
        return {"connected": True, "source": creds["source"],
                "detail": f"Connected as {name} (via {creds['source']})."}
    except httpx.HTTPError as e:
        return {"connected": False, "source": creds["source"],
                "detail": f"Calendly reachability check failed: {e}"}


def _request(method: str, path: str, token: str,
             *, params: Optional[Dict] = None, json: Optional[Dict] = None) -> Dict[str, Any]:
    url = path if path.startswith("http") else _CALENDLY_BASE + path
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.request(
                method, url,
                headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json"},
                params=params, json=json,
            )
    except httpx.HTTPError as e:
        raise SalesProviderError(f"Calendly request failed: {e}") from e
    if resp.status_code == 401:
        raise SalesAuthError("Calendly rejected the token (expired or invalid). Reconnect Calendly.")
    if resp.status_code >= 400:
        raise SalesProviderError(
            f"Calendly API error {resp.status_code}: {resp.text[:300]}"
        )
    try:
        return resp.json()
    except Exception as e:
        raise SalesProviderError(f"Calendly returned a non-JSON response: {e}") from e


def book_meeting(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a single-use Calendly scheduling link for a qualified prospect.

    Calendly does not let an API caller place a booking on the invitee's behalf
    (the invitee must pick a slot), so the governed, fully-functional action is
    to mint a one-time scheduling link bound to a real event type and hand it to
    the prospect. This is NOT a placeholder — it is the operative booking path.

    ``params`` may carry an optional ``event_type_uri`` to pin a specific event
    type; otherwise the account's first active event type is used.

    Returns ``{ok, booking_url, booking_ref, event_type, owner, source}``.
    Raises SalesNotConfigured / SalesAuthError / SalesProviderError loudly.
    """
    creds = calendly_credentials()
    token = creds["access_token"]

    me = _request("GET", "/users/me", token)
    resource = (me or {}).get("resource") or {}
    user_uri = resource.get("uri")
    if not user_uri:
        raise SalesProviderError("Calendly did not return a user URI for /users/me.")

    event_type_uri = (params.get("event_type_uri") or "").strip() or None
    event_type_name = None
    if not event_type_uri:
        ets = _request("GET", "/event_types", token,
                       params={"user": user_uri, "active": "true", "count": 1})
        collection = (ets or {}).get("collection") or []
        if not collection:
            raise SalesProviderError(
                "No active Calendly event types found. Create a meeting type in "
                "Calendly before booking."
            )
        event_type_uri = collection[0].get("uri")
        event_type_name = collection[0].get("name")
        if not event_type_uri:
            raise SalesProviderError("Calendly event type is missing its URI.")

    link = _request("POST", "/scheduling_links", token, json={
        "max_event_count": 1,
        "owner": event_type_uri,
        "owner_type": "EventType",
    })
    booking_url = ((link or {}).get("resource") or {}).get("booking_url")
    if not booking_url:
        raise SalesProviderError("Calendly did not return a booking_url for the scheduling link.")

    return {
        "ok": True,
        "booking_url": booking_url,
        "booking_ref": event_type_uri,
        "event_type": event_type_name,
        "owner": resource.get("name"),
        "source": creds["source"],
    }
