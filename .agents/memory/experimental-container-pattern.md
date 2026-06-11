---
name: experimental "referenced but not deployed" container pattern
description: How dev-only/experimental agent code is isolated in this repo — and why not a git branch.
---

# Experimental container = gated-in-trunk, not a branch

For code that should be **usable in development but never run in production**
("referenced but not deployed"), the convention here is:

1. **Live in the main tree** as a self-contained subpackage (e.g.
   `agentledger/experimental/`). Nothing in `src/` imports it.
2. **Runtime gate** — refuse to initialize under a deployment
   (`REPLIT_DEPLOYMENT == "1"`); enabled in dev by default; `AGENT_MEMORY_STACK`
   env var force-enables/disables; `force=True` is the explicit, auditable bypass.
3. **Dependency isolation** — heavy deps go in via `uv pip install` (dev venv
   only), NOT `pyproject.toml`, so the production closure never installs them.
   Imports are lazy so the module is safe to import even where deps are absent.
4. **Reproducible in dev** via `scripts/post-merge.sh` (re-installs the dev-only
   deps after each merge).
5. **gitignore only the runtime data** (generated DB files), never the code.

**Why not a git branch:**
- "Referenced" and "branch-only" are mutually exclusive — main code can't import
  code that isn't in its tree.
- A working tree is on ONE branch at a time: to *use* branch code in dev you'd be
  on that branch (not isolated), and you'd lose your main work. Branch isolation
  and dev-usability can't both hold.
- Replit version control is platform-managed/trunk-style (commits + merges to the
  main app); long-lived side branches drift, rot, and lose refactor/type/test
  coverage. The native "safe experiment / rollback" tool is checkpoints.
- The isolation axis that matters is **dev-usable vs prod-inert** (the gate), plus
  **blast-radius** (own subpackage, own data dir, namespaced `agent_memory`
  Postgres schema) — not branch-vs-branch.

**Why DuckDB/psycopg instead of the spec's LanceDB/LangGraph:** LanceDB has no
wheel for newer Python here and pulls heavy native deps — DuckDB's
`array_cosine_similarity` over `FLOAT[N]` gives an embedded vector store in the
same engine used for the AST graph (which `ATTACH`es the SQLite ledger read-only
for zero-copy joins). LangGraph is skipped because WaveOrchestrator already owns
that role; the Postgres `CheckpointStore` schema is shaped so a LangGraph
`PostgresSaver` can be dropped on later.
