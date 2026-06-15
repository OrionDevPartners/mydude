"""
Auto-Siphon Acquisition Loop — closed-loop capability acquisition.

When the broker encounters an unmet capability deficit (after DevGuard's
dedup check finds no in-codebase equivalent), this package opens an
acquisition job that:

  1. Fetches candidate packages from provider-agnostic registries (PyPI/npm)
     and harvests supporting knowledge from web/API docs — in parallel.
  2. Verifies each candidate inside a sandboxed, secret-free subprocess.
  3. Routes passing candidates through the swarm's governance envelope
     (compliance ≥ 0.80, hallucination_risk ≤ 0.25) AND raises an explicit
     GovernanceProposal for operator approval.
  4. Only after sandbox pass + governance pass + operator approval is a
     candidate registered as a live capability.
  5. Every outcome is audited (secret-free) and successful acquisitions are
     distilled into long-term memory via the existing siphon.

Kill switch: ENABLE_AUTO_SIPHON_ACQUISITION=true (default: false).
When off, behavior reverts to detect-and-alert only.
"""
