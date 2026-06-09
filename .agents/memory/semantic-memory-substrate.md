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
- The `_local_cache` in LocalMemoryAdapter is process-local and lost on restart; DB persistence is a known gap (follow-up task)

## Vendored library approach
Both Cognee and Mem0 are vendored as self-contained Python modules under `src/vendors/`, not pip dependencies. This avoids adding heavy transitive deps (neo4j, qdrant, etc.) while giving full control. The trimmed implementations cover only KG store + TF-IDF query + entity extraction (Cognee) and add/search/get_all/delete + local-file fallback (Mem0).
