---
name: Semantic memory substrate
description: Key lessons from wiring vendored Cognee/Mem0 semantic memory into the MyDude.io swarm stack.
---

## Route conflict: existing /memory in routes_governance.py
When adding a new /memory route via a dedicated router, the old route in `routes_governance.py` takes precedence because `governance_router` is included first in `app.py`. The fix is to remove the route from governance and have the new dedicated router own it entirely.

**Why:** FastAPI routes first-match, and router include order in `app.py` determines precedence.

**How to apply:** Always grep for existing routes before adding a new router. When moving a route, delete the old one in the same commit.

## Temporal contradiction gate
TF-IDF cosine similarity scores ~0.0 for "finish by Friday" vs "deadline is Monday" because the sentences share no content words. The condition `sim > 0.25 and has_temporal` therefore never fires.

**Fix:** Flag temporal conflicts independently of the cosine gate — `has_negation or has_temporal` — because deadline/completion trigger words + different day names is already a specific enough signal.

**File:** `src/swarm/provenance.py` — `ConsistencyChecker.check_consistency()`

## Memory substrate architecture
- `src/vendors/cognee/` — local KG (JSON file on disk, TF-IDF indexed)
- `src/vendors/mem0/` — cloud store (Mem0 API when MEM0_API_KEY set, local-file fallback otherwise)
- `src/memory/substrate.py` — process-wide singleton via `get_substrate()`; call `write_claim()` and `recall()` only through this
- DB persistence is the durable source of truth: both adapters + the audit ring now load from / flush to PostgreSQL via `src/memory/db_store.py` (`memory_entries` + `memory_audit_logs`). Entries are keyed by (memory_id, adapter="local"|"cloud"); adapters rehydrate caches on init so memory survives restarts. db_store degrades safely (logs, never crashes) when the DB is down.
- KG ingest pitfall: `LocalMemoryAdapter.add()` → `_query.ingest()` → `KnowledgeGraph.add_node()._save()` is SLOW (rewrites the whole KG JSON per node) and can appear to hang in this container — it is pre-existing and unrelated to DB persistence. To test the DB/cache path fast, toggle the adapter's KG off (`_available=False; _query=None; _graph=None`) so `add()` only does cache + db_store.

## Vendored library approach
Both Cognee and Mem0 are vendored as self-contained Python modules under `src/vendors/`, not pip dependencies. This avoids adding heavy transitive deps (neo4j, qdrant, etc.) while giving full control. The trimmed implementations cover only KG store + TF-IDF query + entity extraction (Cognee) and add/search/get_all/delete + local-file fallback (Mem0).

## KnowledgeGraph lock must be reentrant
`src/vendors/cognee/graph.py` module-level `_LOCK` must be a `threading.RLock`, not a plain `Lock`. `add_edge()` calls `add_node()` for missing endpoints **while already holding the lock**, so a non-reentrant lock deadlocks.

**Why:** ordinary prose often extracts relations whose endpoint entities aren't yet nodes (e.g. extractor returns relations but 0 entities). That path silently HANGS `write_claim()` / any KG ingest — not an error, a full deadlock.

**How to apply:** keep `_LOCK = threading.RLock()`. If adding new public KG methods that take `_LOCK`, never assume the lock is free — internal helpers may already hold it.

## Hermetic memory tests
Set `COGNEE_DATA_DIR` and `MEM0_DATA_DIR` to a temp dir and pop `MEM0_API_KEY` **before importing** any `src.memory.*` / `src.vendors.cognee/mem0` module — those bind data-file paths at import time. See `tests/test_semantic_memory.py`. Cross-session recall is tested by writing with one `MemorySubstrate()`, dropping it, and recalling from a fresh instance (restores cache from the persisted KG JSON).
