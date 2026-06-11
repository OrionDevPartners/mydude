---
name: Agent Ledger
description: Isolated SQLite registry agents query to track packages/providers and their layer/container/function placement when project context is too heavy.
---

# Agent Ledger (`agentledger/`)

An **isolated SQLite DB** (`agentledger/agent_ledger.db`, own SQLAlchemy engine in
`agentledger/db.py`) that indexes the whole project so agents don't have to hold it
all in context. **Use it first** when you need to know what a package/provider is for
or where it lives.

**Query it:** `python -m agentledger.query <summary|layers|containers|providers|packages|capability <slug>|where <provider|package> <name>|container <slug>|search <text>>`

**Rebuild after structural change:** `python -m agentledger.seed` (idempotent — drops
+ repopulates from real state, so it never goes stale).

**Why:** project context is too heavy to track from memory; the user explicitly asked
for a ledger to make packages/providers + their architectural placement queryable.

**How it stays real (governance pillar #1 — no placeholders):** all rows are *derived*,
never invented — packages from `pyproject.toml` + `frontend/package.json`; containers
from the filesystem; functions + placements from an `ast` import scan; providers from a
curated catalog that is verified against the source tree before insert (unverified ones
get status `planned`, not `active`).

**Schema shape (`agentledger/models.py`):** Layer→Container→Function hierarchy; Package
+ Provider catalog; Capability ⇄ Provider M2M with primary/fallback_tier (pillar #2
provider-agnostic); SecretRequirement stores only env-var/vault-key *references* +
sourcing order, never values (pillar #3); polymorphic Placement edges (package|provider
→ layer/container/function) carry evidence + criticality; ComponentDependency typed
edges; append-only LedgerEvent audit.

**Isolation rule:** never wire this into the FastAPI app or `src/database.py`. It is
agent infrastructure, separate from user data.
