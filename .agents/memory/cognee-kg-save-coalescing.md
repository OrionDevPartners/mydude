---
name: Cognee KG save coalescing
description: Why/how the local knowledge-graph JSON save is deferred + coalesced, and the constraints to keep.
---

# Cognee KnowledgeGraph save coalescing

The vendored KG (`src/vendors/cognee/graph.py`) persists as one JSON file. Every
mutation used to call `_save()` which rewrote the *entire* file synchronously. A
single `write_claim()` runs one ingest (extracts many entities + relations) plus
an `add_node`, so it triggered N full-file rewrites and could hang (>50s) on a
large graph.

**Rule:** `_save()` must never write inline. It marks dirty and schedules a single
debounced background flush (`COGNEE_SAVE_DEBOUNCE_SEC`, default 0.5s). `flush()`
snapshots under `_LOCK` then writes outside it under `_IO_LOCK` (unique temp file
+ os.replace). `batch()` (re-entrant context manager) groups many mutations into
one save; `LocalMemoryAdapter.add()` wraps ingest + add_node in `with graph.batch():`.

**Why:** per-node whole-file rewrites are O(graph size) × O(nodes-per-write); that
is the freeze. The DB store (`src/memory/db_store.py`) is the durable source of
truth, so debouncing the JSON index is safe.

**How to apply / constraints:**
- Don't reintroduce a synchronous write in `add_node`/`add_edge`/`apply_decay`/`remove_node`.
- Each KnowledgeGraph instance captures its data dir/file at construction
  (`self._data_dir`/`self._graph_file`) — a debounced timer must target the
  instance's file, not the module global, or test fixtures that swap-then-restore
  `_DATA_DIR`/`_GRAPH_FILE` (and the real dev graph.json) get polluted.
- `atexit.register(flush)` covers clean shutdown; an unclean crash mid-debounce
  can leave graph.json lagging the DB until startup reconciliation exists.
