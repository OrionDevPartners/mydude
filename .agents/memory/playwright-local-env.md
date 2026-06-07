---
extra: When adding interactive browser actions (login_page/cancel_action/etc.), implement them on the Browserbase backend too, not just LocalPlaywright — Browserbase is the prod path and a missing override silently falls back to the base "does not support" default. A smoke test lives at tests/test_browser_interactive.py.
name: Local Playwright in this container
description: Why the local Chromium browser backend is unavailable in the Replit container and what the real path is.
---

Installing the Playwright Chromium build succeeds (`python -m playwright install chromium` downloads to the workspace `.cache/ms-playwright`, which is gitignored), but the binary fails to launch with `error while loading shared libraries: libnspr4.so: cannot open shared object file`. The headless-shell needs system libs (nspr/nss/etc.) not present by default.

**How to apply:** Treat the local Playwright backend as optional/best-effort. Browserbase (cloud Chromium over CDP) is the production browsing path — it uses the same Playwright `page.route`/`goto` API, so logic verified against the contract holds there. Don't block delivery on a live local-browser test; verify policy/route logic with unit/route suites instead. If a local browser is truly needed, install the missing system libraries via the package-management skill (system deps), not just the playwright npm/pip install.

**Live-validated (2026-06-07):** the Browserbase path was driven end-to-end against a real two-step (email→password) SPA sign-in — login + account-view succeeded, policy allow-list gating held. The two-phase *cancel* was NOT live-tested (it is irreversible; no throwaway cancellable account). Honest "needs you" reporting (CAPTCHA/OTP/SSO) is correct in the contract suite. **Non-obvious gotcha:** real sign-in pages are SPAs whose fields are absent at `domcontentloaded` and hydrate a beat later — the login heuristic must `wait_for_selector` the form to mount before filling, or it silently no-ops and misreports "no password field / needs you" (fixed in `src/browser/backends.py` `_do_login`/`_wait_for_login_form`).
