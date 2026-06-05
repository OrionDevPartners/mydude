---
name: Capability governance gates
description: Non-obvious rules for enforcing the browser/SSH capability allow-lists in MyDude so they cannot be bypassed.
---

The browser_open and ssh_run/ssh_read_history/ssh_fetch_code capabilities flow through broker → PolicyEngine → integrations. Governance lives in `src/swarm/policy.py`. Three bypasses were found and fixed; keep them closed.

**SSH command gating must do more than a destructive-substring + first-token allow-list.**
- A first-token-only allow-list is bypassable: `whoami\nrm -rf /` (newline), `whoami & curl evil` (single `&`), redirection (`>`,`<`), globbing (`*`,`?`), subshell/var (`$(`,`` ` ``,`$`). Reject newlines/CR outright, block a broad metachar set, then `shlex.split` and check the parsed first token (so quoting can't smuggle the executable past the check).
- Allow-listed powerful binaries still need argument-level guards: `sqlite3` can run dot-commands (`.shell`,`.import`) and SQL (`load_extension`,`ATTACH`); `defaults write` mutates state. Reject sqlite3 tokens starting with `.`, risky flags (`-cmd/-init`), and write/exec SQL keywords; reject `defaults write/delete/rename`.
- **Why:** the remote Mac executes the command string in a shell, so any shell metacharacter is an injection vector.

**Browser domain allow-list must be enforced BEFORE each navigation hop, not after.**
- A post-navigation final-host check is a TOCTOU/SSRF gap: the off-list (possibly internal) host already received the request and returned content before you block it.
- Enforce at the Playwright layer: `page.route("**/*", handler)` that, for `request.is_navigation_request()` on `page.main_frame`, parses the host and `route.abort()`s off-list hops before dispatch; fail closed on interceptor errors. `PolicyEngine.is_host_allowed()` is the single source of truth, passed down as an `allow_host` predicate (browser package must not import policy).
- `BrowserResult.blocked` distinguishes a policy block from a backend failure; the engine must NOT fail over to another backend on a block (it would retry the forbidden navigation).

**Audit blocked attempts, not just executions.** Denials are logged at the broker level (`_AUDITED_CAPABILITIES`) with status='blocked', in addition to integrations-level execution audits, so the CapabilityAuditLog reflects the full governance picture.
