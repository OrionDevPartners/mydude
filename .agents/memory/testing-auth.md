---
name: Testing auth-gated routes
description: How to e2e-test MyDude routes now that login is per-user (bcrypt) and the UI is a SPA.
---

Login is now **per-user**: a `User` row (username + bcrypt hash, `is_active`, `is_admin`)
authenticated via `authenticate_user`, with the session cookie carrying a `uid`.
`resolve_session` re-validates the user (active + exists) on every request, so it
**rejects legacy `{"authenticated": True}` cookies that lack a `uid`** — the old
session-minting trick no longer works.

On first boot `seed_admin_user()` creates an `admin` account from `ADMIN_PASSWORD`
(default `"admin"` if unset). This is the migration path off the old shared password.

**Dev bypass:** when `DEV_AUTH_BYPASS=1` (the "Vite Dev" workflow, and the dev
container generally), `resolve_session` returns a synthetic `dev-bypass` admin
identity for *every* request regardless of cookies. Consequences:
- `/api/me` returns `username: "dev-bypass"` even right after logging in as `admin`.
- KeyAuditLog `actor` columns record `dev-bypass`, not the real user. Attribution
  is still structurally correct; the identity is just the bypass one in dev.
- The SPA Login page never shows (getMe always 200), so you can't screenshot the
  real login flow while bypass is on.

**How to test:**
- Backend flows (login, user CRUD, audit attribution) — drive `/api/*` with `curl`
  against the running app on :5000; form-encoded POST, JSON responses, `HTTPException`
  `detail` is the user-facing message. This works under dev bypass.
- To exercise the *real* login UI / per-user attribution, you'd need bypass off and
  the `admin` password — reserve the Playwright `runTest` skill for that.

**Why:** browser e2e of login is blocked by dev bypass + the secret password; curl
against the JSON API gives reliable coverage of the auth and attribution logic.
