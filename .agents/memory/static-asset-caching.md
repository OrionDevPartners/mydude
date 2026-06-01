---
name: Static asset caching in Replit preview
description: Stale CSS/JS in the Replit preview pane despite correct source; how to force fresh fetches
---

# Stale static assets in the Replit preview

The Replit preview pane (and its screenshot tool) can serve a **stale cached copy**
of `/static/*` assets even after the workflow restarts. Symptom: source CSS is
provably correct, but the rendered page reflects an older stylesheet — e.g. rules
that should be `display:none` still render, default/unstyled elements appear.

**Why:** FastAPI `StaticFiles` (Starlette) sends no `Cache-Control` by default, so
the browser/proxy applies heuristic caching and never re-fetches the changed file.

**How to apply:** Two-part fix, both used together:
1. A dev-only middleware that sets `Cache-Control: no-store, max-age=0` on responses
   whose path starts with `/static` (gate on `REPLIT_DEPLOYMENT != "1"`).
2. A version query string on the asset URL (e.g. `style.css?v=N`) to force a fresh
   fetch past any already-cached entry. Bump `N` when needed; no-store keeps it fresh after.

Don't waste time hunting CSS specificity/`!important` when the symptom is "correct
source, wrong render" — suspect caching first and force a fresh fetch.
