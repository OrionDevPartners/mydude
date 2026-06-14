---
name: Avatar connection-token persistence
description: Avatar session connection descriptors carry provider room tokens — never persist them; return them only in the immediate response.
---

# Avatar connection-token persistence

A negotiated avatar session connection descriptor (HeyGen LiveKit `access_token`,
WHEP bearer, TURN credentials, SDP offer) is a **room secret**. It must reach the
browser but must NEVER be written to the DB, logs, or the audit trail.

**The rule:** `_activate()` in `src/avatar/sessions.py` returns the FULL descriptor
to the immediate start/consent HTTP response only; it persists ONLY a non-secret
routing subset via `_persistable_connection()` (allowlist — currently just
`session_id`). `start_session`/`record_consent` attach the full descriptor to the
response in memory. A later read (`get_session(include_connection=True)`) sees only
the sanitized `connection_json`.

**Why:** governance pillar #3 (separate provider from secrets) + the live-call done
criterion "tokens NEVER logged/audited/persisted". The earlier code wrote the whole
descriptor (incl. token) to `connection_json` — a real leak.

**How to apply:** the server only needs `session_id` later (for HeyGen
`streaming.start` via `bridge._heygen_start_stream`, which posts ONLY `{session_id}`
with the server-side key). If a new provider needs another non-secret routing field
server-side, ADD it to the `_persistable_connection` allowlist — never widen it to
pass tokens. Use an allowlist (fail-safe), not a denylist, so a new provider's token
field can't leak by omission. Browser-side, the descriptor lives only in React state
(`AvatarCall.tsx`) and is torn down on End/unmount.
