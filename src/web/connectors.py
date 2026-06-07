"""Runtime helper to read live Replit connector / integration status.

Replit injects the connector credential proxy host plus an identity token into
the environment. We query it (without requesting secrets) to determine which
integrations the user has connected at the account level.
"""

import os
import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _auth_token():
    ident = os.environ.get("REPL_IDENTITY")
    if ident:
        return "repl " + ident
    renewal = os.environ.get("WEB_REPL_RENEWAL")
    if renewal:
        return "depl " + renewal
    return None


def proxy_available():
    return bool(os.environ.get("REPLIT_CONNECTORS_HOSTNAME") and _auth_token())


def get_connection_status(connector_names):
    """Return {connector_name: {"connected": bool, "created_at": str|None}}."""
    result = {name: {"connected": False, "created_at": None} for name in connector_names}
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    token = _auth_token()
    if not hostname or not token or not connector_names:
        return result
    try:
        query = urllib.parse.urlencode({"connector_names": ",".join(connector_names)})
        url = "https://%s/api/v2/connection?%s" % (hostname, query)
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "X_REPLIT_TOKEN": token},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        items = payload.get("items") or payload.get("connections") or []
        for item in items:
            name = item.get("connector_name") or item.get("name")
            if name in result:
                result[name] = {
                    "connected": True,
                    "created_at": item.get("created_at"),
                }
    except Exception as e:
        logger.warning("Connector status query failed: %s", e)
    return result


def get_access_token(connector_name):
    """Return a fresh OAuth access token for ``connector_name`` via the proxy.

    Returns the token string, or None if the connector is not connected / the
    proxy is unavailable. The token is fetched fresh on every call and is NEVER
    cached here — tokens expire and the proxy refreshes them. The caller must
    not log or persist the returned token.
    """
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    token = _auth_token()
    if not hostname or not token or not connector_name:
        return None
    try:
        query = urllib.parse.urlencode({
            "include_secrets": "true",
            "connector_names": connector_name,
        })
        url = "https://%s/api/v2/connection?%s" % (hostname, query)
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "X_REPLIT_TOKEN": token},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        items = payload.get("items") or payload.get("connections") or []
        for item in items:
            settings = item.get("settings") or {}
            access_token = (
                settings.get("access_token")
                or ((settings.get("oauth") or {}).get("credentials") or {}).get("access_token")
            )
            if access_token:
                return access_token
    except Exception as e:
        logger.warning("Connector token query failed: %s", e)
    return None
