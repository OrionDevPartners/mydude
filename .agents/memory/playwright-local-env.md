---
name: Local Playwright in this container
description: Why the local Chromium browser backend is unavailable in the Replit container and what the real path is.
---

Installing the Playwright Chromium build succeeds (`python -m playwright install chromium` downloads to the workspace `.cache/ms-playwright`, which is gitignored), but the binary fails to launch with `error while loading shared libraries: libnspr4.so: cannot open shared object file`. The headless-shell needs system libs (nspr/nss/etc.) not present by default.

**How to apply:** Treat the local Playwright backend as optional/best-effort. Browserbase (cloud Chromium over CDP) is the production browsing path — it uses the same Playwright `page.route`/`goto` API, so logic verified against the contract holds there. Don't block delivery on a live local-browser test; verify policy/route logic with unit/route suites instead. If a local browser is truly needed, install the missing system libraries via the package-management skill (system deps), not just the playwright npm/pip install.
