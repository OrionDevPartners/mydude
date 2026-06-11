---
name: DevGuard capability-request wiring
description: How the dev-gated DevGuard dedup alarm hooks into the live async CapabilityBroker and the Governance inbox without breaking prod or the hot path.
---

# DevGuard ↔ broker / Governance-inbox wiring

DevGuard (the dev-gated semantic-dedup + lifecycle-guardian engine) runs a
"never rebuild an existing capability" dedup alarm when an agent requests a
*new* capability. The wiring decisions below are non-obvious and were locked
with the architect.

## Rules (apply when extending DevGuard or any dev-gated subsystem into live code)

- **Hook at the broker's FINAL stub branch only.** Unknown capabilities get the
  default contract (no required fields/preconditions) and pass the policy gate,
  so a genuinely-new capability always falls through to the stub return — that
  is the correct single integration point. Known handlers must not trigger it.
- **Cheap gate pre-check BEFORE any heavy import.** Call `gate.is_enabled()`
  (pure stdlib) first and return `[]` in production *before* importing
  scanner/index (DuckDB + fastembed). Otherwise prod pays the import cost.
  `is_enabled()` can raise `ValueError` on a malformed `AGENT_MEMORY_STACK` —
  catch it and treat as disabled, never crash the broker.
- **Fire-and-forget off the async hot path.** First dedup call builds the index
  (model load + ~1300 units, tens of seconds), all synchronous. Never `await`
  it inside `broker.request()`; schedule via `run_in_executor` and swallow
  errors. Alert-only means a delayed/failed alarm must never affect the request.
- **One aggregated `SentinelEvent` per check, not one per alert.** Mirror the
  broker's existing `contract_violation` pattern.
- **Real in-app inbox surface = `SentinelEvent`** via
  `src.swarm.error_metrics.record_sentinel_event` (best-effort). The agent-inbox
  has no Python write API (JS read-only), so don't try to write to it.

## Capability/provider indexing — Option A, NOT pseudo-units
**Why:** the dedup index stores AST `CodeUnit`s with exact/normalized hashes and
an embedder-id consistency check. Injecting synthetic "capability" pseudo-units
pollutes match-type semantics and only survives a rebuild if re-synthesized.
**How to apply:** build a name registry from `capability_contracts.all_contracts()`
(provider-agnostic source) + the broker's handler set; a normalized-name hit is
an exact "already exists" match (synthesize a `DuplicateAlert`). Run the semantic
`check_duplicate` only on credential-safe descriptor text (capability name +
contract description + non-secret params like description/source — never raw
url/command, which can carry tokens). Note: short descriptor text vs code
embeddings rarely crosses the 0.85 threshold, so the registry path is the
high-signal alarm; the semantic path is a best-effort bonus.

## Don't over-scope
GuardianLedger is NOT wired into the capability path — alert-only needs only the
console/JSONL/SentinelEvent surfaces; the ledger opens its own pool and its sync
bridge raises from a running loop.
