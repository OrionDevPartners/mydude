---
name: Connector OAuth token retrieval in Python
description: How to get a Replit connector's OAuth access token from Python (no JS snippet exists for it).
---

# Getting a Replit connector access token from Python

`viewIntegration`/`addIntegration` only render a **JavaScript** `getUncachable…Client()`
snippet — there is no Python snippet. For Python (this project is FastAPI), fetch
the token yourself from the connector credential proxy.

**Rule:** GET `https://$REPLIT_CONNECTORS_HOSTNAME/api/v2/connection?include_secrets=true&connector_names=<name>`
with header `X_REPLIT_TOKEN` = `repl <REPL_IDENTITY>` (fallback `depl <WEB_REPL_RENEWAL>`).
The access token is at `items[0].settings.access_token` **or**
`items[0].settings.oauth.credentials.access_token` — handle both shapes.

**Why:** without `include_secrets=true` the proxy returns connection status only,
no token. `src/web/connectors.py` already had a status-only helper; the token
helper (`get_access_token`) lives beside it.

**How to apply:** call `get_access_token(<connector_name>)` fresh on every use —
never cache; tokens expire and the proxy refreshes them. Connector names are the
short slug (e.g. `google-mail`), not the `ccfg_...` id.
