---
name: Developer access gate
description: How the one-click dev login works and why the production gate must never be weakened.
---

# Developer access gate

## The rule
The login screen MUST show a "Developer sign-in" button in the dev/workspace
environment. No password required. The button and its backing endpoints are
hard-gated so they cannot function when `REPLIT_DEPLOYMENT=1`.

## How it works
- `GET /api/auth/dev-info` (public) — returns `{ available: bool }` where
  `available = not _in_deployment()`. Login.tsx polls this on mount.
- `POST /api/auth/dev-login` — 403 in deployment; otherwise signs and sets a
  session cookie using `make_dev_session_token()` (the dev-bypass identity dict
  serialized with the app's own URLSafeTimedSerializer).
- `resolve_session()` accepts a dev-bypass cookie (`uid=None, dev_bypass=True`)
  only when `_in_deployment()` is False. The cookie is structurally identical to
  a normal session cookie so the SPA's AuthContext recognises it normally.

## Why
Before this, the developer had to know to set the hidden `DEV_AUTH_BYPASS` env
var or know the admin password. No on-screen hint → dead end at the login wall
when testing the site. The one-click button makes dev access reliable and
obvious without weakening production security.

**Why:** Dev velocity: developers must be able to test their own app without
hunting for credentials.

**How to apply:** Any future auth gate, beta gate, or invite wall must include
this affordance. The double gate (`_in_deployment()` check both in the endpoint
and in `resolve_session()`) is the required pattern — never skip either check.
