"""Self-evolving prompt engine (Task #69).

A governance-gated DSPy layer that turns hardcoded swarm prompts into versioned,
optimizable programs. Lightweight, dspy-free metadata lives in ``specs`` so the
startup seed/recovery path never pays the dspy import cost; the heavy DSPy
machinery (signatures, lm bridge, optimizers) is imported lazily by the runtime
and optimization-service code paths only.

Pillars honored:
  * No placeholders / fail-loud — optimizer + provider errors surface as failed
    runs with an error message; never a silent fallback to an unverified prompt.
  * Provider-agnostic — all model calls go through the existing provider adapter
    registry (src/providers), never a hardwired vendor.
  * Governed inference — evolved prompts go live ONLY through the existing
    GovernanceProposal/Vote/Enactment gate; rollback is a direct, audited action.
"""
