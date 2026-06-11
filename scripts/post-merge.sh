#!/bin/bash
set -e

# Install Python dependencies from the existing uv.lock (idempotent, non-interactive).
# Use --frozen: a plain `uv sync` re-resolves against the unbounded requires-python
# (">=3.11") and fails on the Python 3.14 split, where a transitive dependency of
# optuna has no wheel. The committed lock is known-good (the app runs on it), so we
# install from it directly instead of re-resolving.
uv sync --frozen

# Experimental, development-only embedded memory stack (agentledger/experimental).
# These deps are intentionally NOT in pyproject.toml / the production dependency
# closure: the stack is "referenced but not deployed" — gated off in production.
# Installing them here keeps the experimental container reproducible across dev
# rebuilds without ever shipping to a deployment. Idempotent + non-interactive.
uv pip install duckdb "psycopg[binary,pool]"

# DevGuard (agentledger/experimental/devguard) dev-only deps: fastembed (ONNX
# MiniLM embeddings, no torch), pyarrow (fast DuckDB bulk insert of vector
# columns), watchdog (optional real-time file watcher). Also dev-gated off in prod.
uv pip install fastembed pyarrow watchdog

# Rebuild the Agent Ledger (agentledger/) from current project state so it never
# drifts after a merge that adds packages/providers or restructures src/. The seed
# is idempotent (drops + rebuilds its own isolated SQLite file) and records a
# LedgerEvent audit row for each rebuild. This is agent-only dev infrastructure and
# is NOT part of the app's runtime, so a rebuild failure must not abort the merge or
# block the deps/migrations above — warn loudly instead of failing hard.
if python -m agentledger.seed; then
    echo "[post-merge] Agent ledger rebuilt from current project state."
else
    echo "[post-merge] WARNING: agent ledger rebuild failed; ledger may be stale. Run 'python -m agentledger.seed' manually." >&2
fi

# Note: database schema is auto-migrated on app startup via _sync_missing_columns,
# so no separate migration step is required here.
