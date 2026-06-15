---
name: Zero-token structural routing
description: How the pre-swarm zero-token router is wired into WaveOrchestrator and surfaced to the UI as STRUCTURAL_ROUTING.
---

# Zero-token structural routing

The zero-token / structural router (`src/swarm/zero_token_router.py`,
`trajectory_router.py`) decides — before the LLM swarm spends any tokens —
whether a goal structurally matches an indexed capability strongly enough to be
dispatched mechanically through the governed broker.

**Key fact:** the router existed but was *dormant* — nothing called it during a
run. It is now invoked from `WaveOrchestrator.run()` as a pre-swarm gate that
records a `STRUCTURAL_ROUTING` decision dict (hit or miss) and short-circuits the
run with a compact governed envelope on a genuine dispatch.

**Why dispatch is rare/safe (do not "fix" this as a bug):**
- `broker.request()` runs contract validation + policy gate BEFORE dispatch.
  `async_route` passes only `{source, intent}` (no real params), so any
  capability needing params fails contract validation → `dispatched=False` →
  the full swarm runs. This is the intended fail-safe.
- The score threshold is high (~0.92), so hits are uncommon by design.
- `_evaluate_structural_routing` is wrapped fail-safe: any error returns a
  decision with `dispatched=False` (+ an `error` note) so the swarm always
  proceeds. Never let a routing failure raise.

**How it's surfaced:**
- Miss: `final["STRUCTURAL_ROUTING"] = decision` (only when non-empty, so older
  runs without the field render gracefully).
- Hit: `_build_zero_token_envelope` returns SYNTHESIS + SYNTHESIS_SOURCE=
  `zero_token_router` + STRUCTURAL_ROUTING + JURISDICTION.
- API `_parse_task` lifts it onto `structural_routing`; the SPA TaskDetail
  "Routing" card and TaskHistory zero-token badge/filter read it.

**How to apply:** when touching the router or run() short-circuit, keep the
fail-safe contract (never raise, default to running the swarm) and keep the
decision-dict keys stable (dispatched, eligible, capability, score, threshold,
embedding_backend, trajectory{dominant_category,dominant_score,hazard_hints},
optional tool_output/error) — the UI and `tests/test_structural_routing.py`
depend on that shape.
