---
name: Outbound write endpoints (FastAPI)
description: How async endpoints that make blocking outbound calls behind an approval gate must be wired in this app.
---

# Outbound write endpoints must offload + lock

Any `async` FastAPI endpoint in this app that makes a **blocking** outbound call
(httpx to QuickBooks/Plaid/etc.) must run the blocking work via
`asyncio.to_thread(...)`, mirroring the `/finance/sync` endpoint. Calling the
blocking client directly inside the coroutine stalls the whole event loop for the
duration of the network call.

If the work is a **two-phase approval gate** (request → confirm) with a
check-then-execute body, add a row lock (`.with_for_update()`) on the request row
inside the confirm function once you offload it.

**Why:** while the blocking call ran inline on the event loop, the check-then-execute
was only accidentally race-safe (nothing else could interleave). The moment you move
it to a worker thread, two concurrent confirms can both pass the `pending_confirm`
status check and double-fire the irreversible outbound write. The lock makes the
status check + state transition atomic.

**How to apply:** new finance (or similar) write-back / outbound-action endpoints:
1. `await asyncio.to_thread(fn, db, ...)` in the router.
2. `db.query(Model).filter(...).with_for_update().first()` at the top of the gated
   `confirm_*` function, before re-validating the STORED payload (never client input)
   and executing.
3. Keep every transition (requested / blocked / executed / failed / rejected) in the
   audit log.
