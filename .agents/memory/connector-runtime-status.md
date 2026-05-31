---
name: Runtime connector status
description: Reading live Replit integration/connection status from inside the running app.
---

The app can read live Replit integration status at runtime (not just via the agent's `listConnections`). Env vars `REPLIT_CONNECTORS_HOSTNAME`, `REPL_IDENTITY` (and `WEB_REPL_RENEWAL` in deployments) are injected.

Query: `GET https://{REPLIT_CONNECTORS_HOSTNAME}/api/v2/connection?connector_names=a,b` with header `X_REPLIT_TOKEN: "repl " + REPL_IDENTITY` (or `"depl " + WEB_REPL_RENEWAL`). No connections → returns empty; a 401 in dev is expected when nothing is connected — degrade gracefully to "not connected".

Connector slugs (for proxy + searchIntegrations `ccfg_<slug>_`): stripe, github, google-mail, google-calendar, google-sheet, google-docs, google-drive, notion, slack, twilio, sendgrid. OpenAI/Anthropic are blueprints (API-key), not OAuth connectors.

**Why:** Actual OAuth connect can't be initiated by the app itself — it requires the agent's `proposeIntegration` (exits loop) or the Replit Integrations pane. The app can only *display* status and direct the user.
