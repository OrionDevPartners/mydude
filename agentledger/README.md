# Agent Ledger

An **isolated registry database for Replit agents** working on MyDude.io. It exists
because this project's context is too heavy to track from memory alone. It records,
in one queryable place, every package and provider the project uses and **where each
one lives** in the architecture (layer → container → function).

It is deliberately **isolated from the application**:

- Its own SQLite file (`agentledger/agent_ledger.db`) — never the app's PostgreSQL.
- Its own SQLAlchemy engine/`Base` (`agentledger/db.py`) — never `src/database.py`.
- Not imported by the FastAPI app. It is agent infrastructure, not user data.

## What it tracks (advanced schema → `agentledger/models.py`)

| Area | Tables |
|------|--------|
| Taxonomy (3-level hierarchy) | `layers` → `containers` → `functions` |
| Catalog | `packages` (python/node/system), `providers` |
| Provider-agnostic abstraction | `capabilities`, `provider_capabilities` (primary + fallback tiers) |
| Secret separation (refs only, never values) | `secret_requirements` |
| Placement (the heart) | `placements` — polymorphic package\|provider → layer/container/function, with evidence + criticality |
| Dependency graph | `component_dependencies` (typed edges) |
| Audit | `ledger_events` (append-only) |

## Honors the governance pillars

- **#1 No placeholders** — every row is derived from real project state
  (manifests + filesystem + `ast` import scan). Nothing is invented.
- **#2 Provider-agnostic** — providers are mapped to abstract capabilities with
  explicit primary/fallback tiers, so swaps are visible.
- **#3 Secrets separated** — only env-var/vault-key *references* are stored, plus the
  runtime sourcing order (connector proxy → vault → env). Never a secret value.
- **#5 Evolvable schema** — rebuilt from scratch each seed, so it never goes stale.

## Rebuild (idempotent — reflects current reality)

```bash
python -m agentledger.seed
```

Re-run this whenever packages, providers, or the `src/` layout change.

## Query (for agents)

```bash
python -m agentledger.query summary
python -m agentledger.query layers
python -m agentledger.query containers [layer_slug]
python -m agentledger.query providers [kind]
python -m agentledger.query packages [python|node]
python -m agentledger.query capability <slug>          # who fulfils it, by fallback order
python -m agentledger.query where <provider|package> <name>   # placements + evidence
python -m agentledger.query container <slug>           # functions + packages + providers
python -m agentledger.query search <text>              # fuzzy across the ledger
```

Programmatic use: `from agentledger.query import summary` (and friends), or query the
models directly via `from agentledger.db import SessionLocal`.
