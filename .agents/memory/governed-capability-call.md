---
name: Governed non-LLM capability calls
description: How non-LLM capability invocations get audit + allow-list + jurisdiction governance via governed_call.
---

# Governed non-LLM capability calls

Non-LLM capabilities (container_compute, realtime, database, storage…) resolve
through `src/capabilities/resolver.py`. Wrap invocations in `governed_call(
category, method, *args, actor=session, source=...)` (and `governed_resolve()`),
NOT direct adapter calls, so they get the same governance LLM/browser calls get.

What governed_call does:
- Resolves a jurisdiction-permitted adapter; out-of-jurisdiction -> raises
  CapabilityNotAvailable (cloud_shift / exec_locus gate).
- For container_compute run_command/run_command_async: enforces the
  CapabilityBroker command allow-list -> raises `CapabilityDenied`.
- Audits every outcome (blocked/error/ok) with exec_locus + caller identity.
  Audit is fail-soft (never raises).

**Why:** Governance pillar #4 — no ungoverned capability output. Direct adapter
calls bypassed audit + allow-list.

**How to apply:**
- Command allow-list logic lives in `PolicyEngine.evaluate_compute_command()`
  (src/swarm/policy.py), which reuses SSH_ALLOWED_COMMANDS + destructive-pattern
  checks shared with `_evaluate_ssh` via `_validate_command_string()`. It does
  NOT require ENABLE_SSH_CAPABILITY (local subprocess, not SSH).
- `governed_call` is SYNCHRONOUS and REFUSES async/awaitable-returning methods
  (raises CapabilityDenied) — otherwise it would audit "ok" before the coroutine
  ran. For async adapter methods use `governed_call_async` (awaits coroutines,
  offloads blocking methods via asyncio.to_thread, audits the real outcome).
  Shared pre-checks (resolve + allow-list + method lookup) live in
  `_governed_prepare`. The realtime/telephony place_call path uses
  governed_call_async; integrations `_require_capability` resolves via
  governed_resolve so resolution is jurisdiction-audited uniformly.
- For a sync compute call inside an async route, either use governed_call_async
  or `asyncio.to_thread(governed_call, ...)` (see POST
  /api/capabilities/test/compute in src/web/api/router.py).
- Session identity dict keys are `uid`/`username`/`is_admin` (from require_auth);
  governed_call's `actor` reads .get("uid")/.get("username").
- CapabilityAuditLog carries exec_locus, actor_user_id, actor_username
  (auto-migrated via _sync_missing_columns).
