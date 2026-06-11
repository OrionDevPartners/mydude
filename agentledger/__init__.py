"""Agent Ledger — an isolated registry DB for Replit agents.

This package is intentionally separate from the user-facing application (`src/`).
It maintains its own SQLite database (`agent_ledger.db`) — fully isolated from the
app's PostgreSQL — so that agents working on this large project have a queryable
ledger of:

  - every package the project depends on (python + node + system),
  - every external/internal provider the project uses,
  - the architectural taxonomy (layer -> container -> function),
  - where each package/provider is PLACED in that taxonomy (with evidence),
  - the provider-agnostic capability each provider fulfils + the secret it needs,
  - an append-only audit log of ledger changes.

It exists because the project context is too heavy to track from memory alone.
Rebuild it any time from real project state with:  python -m agentledger.seed
Query it with:                                      python -m agentledger.query <command>
"""
