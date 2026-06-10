---
name: SPA is the live UI; Jinja routes are legacy
description: Which frontend actually serves the dashboard, and where to wire UI features.
---

The MyDude dashboard is a **React SPA** (`frontend/src/`, built to `static/spa/`),
not the Jinja templates. `src/web/app.py` only includes the JSON `api_router`
(prefix `/api`) plus a catch-all SPA fallback that serves `static/spa/index.html`
for every non-`/api`, non-`/static` GET. The Jinja routers (e.g.
`src/web/routes_local_models.py` with its `@router.get("/local-models")` HTML
route) and `templates/*.html` are **legacy / dead** — they are not mounted, so
editing them alone changes nothing the user sees.

**Why:** task descriptions still cite the old Jinja files (templates + routes_*)
as the "relevant files," but the app evolved to a SPA. Those Python route modules
are still imported for their *helpers* (the API router reuses `_is_local`,
`_provider_status`, etc.), which is why they aren't deleted.

**How to apply** — a dashboard feature needs three layers, not the Jinja template:
1. Backend write/read logic in the shared module (e.g. `src/providers/*`).
2. A JSON endpoint in `src/web/api/router.py` (form-encoded POST, returns dict;
   raise `HTTPException` for errors — the SPA surfaces `detail` as the message).
3. Frontend: a client fn in `frontend/src/lib/api.ts` + the page in
   `frontend/src/pages/*.tsx`.

Then **rebuild the SPA** so port 5000 (the preview/webview workflow) serves it:
`bash scripts/build-frontend.sh` (installs deps + `vite build` into `static/spa/`).
Dev also runs a "Vite Dev" workflow on 5173 with `DEV_AUTH_BYPASS=1`, but the
preview pane is the built bundle on 5000. Update the Jinja side too only for
parity; it is not what renders.
