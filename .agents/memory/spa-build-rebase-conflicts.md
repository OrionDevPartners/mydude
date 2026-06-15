---
name: SPA build artifact rebase conflicts
description: The committed built SPA (static/spa) can carry git merge/rebase conflict markers that crash the app at runtime; fix by rebuilding, never hand-editing.
---

# Committed SPA build can carry conflict markers → runtime crash

The built React SPA under `static/spa/` (including `index.html` and hashed
`assets/*.js`) is **committed to the repo** and served by FastAPI. Because it is
a generated artifact that changes on every rebuild, a rebase/merge frequently
leaves git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) inside
`static/spa/index.html` and stale/duplicated `<script src=...>` tags.

**Symptom:** the page is blank/crashes; browser console shows
`SyntaxError: Unexpected token '<<'` (the conflict marker parsed as JS) and the
server log shows several `GET /static/spa/assets/index-*.js 404` for chunk
hashes that no longer exist (index.html references multiple conflicting builds).

**Fix:** rebuild — `cd frontend && npm run build` (Vite `outDir: ../static/spa`,
`base: /static/spa/`). It regenerates a single clean `index.html` with one
matching asset hash. Then restart the `Start application` workflow. Verify with
`rg -c "<<<<<<<|>>>>>>>" static/spa/index.html` → none.

**Why:** never hand-resolve the conflict in the built file — you'd pick stale
chunk hashes that 404. The source of truth is `frontend/src`; the build is
derived. Always re-derive it.

**How to apply:** any time the app crashes after a rebase/merge with a JS parse
error or 404s on `static/spa/assets/*.js`, suspect this first and rebuild.
