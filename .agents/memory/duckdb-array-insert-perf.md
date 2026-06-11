---
name: DuckDB array-column insert performance
description: Why row-by-row inserts into a FLOAT[N] vector column hang, and the Arrow bulk fix.
---

# DuckDB FLOAT[N] inserts must be bulk (Arrow), never row-by-row

Inserting into a DuckDB column typed as a fixed-size array (e.g. `FLOAT[384]`,
used for `array_cosine_similarity` vector search) **one row per `execute()` is
pathologically slow — ~0.5s/row** (measured 112s for 200 rows in `:memory:`).
`executemany` is the same cost (row-by-row under the hood). A few hundred rows
looks like a hang; a full code index (>1000 units) blows past any timeout with
no output.

**The fix:** build a `pyarrow.table(...)` for the whole batch with the embedding
column as `pa.array(vecs, pa.list_(pa.float32(), dim))`, `register` it, and
`INSERT INTO t (...) SELECT nextval('seq'), ... FROM <registered>`. This is
~1000x faster (1400 rows in ~0.1s) and `array_cosine_similarity` works
unchanged on the resulting column. `nextval(...)` in the SELECT assigns ids per
row, so no Python-side id generation is needed.

**Why:** DuckDB is columnar/OLAP — each single-row insert of an array param pays
a per-call conversion/row-group cost. Arrow ingestion is the zero-copy bulk path.

**How to apply:** any time you bulk-load vectors into the experimental
`VectorStore` (memory_manager.py), use `add_many()` (Arrow path) — not a loop of
`add()`. `add()` (single row, ~0.5s) is fine only for incremental 1-few-unit
updates. pyarrow is a dev-only dep (installed via scripts/post-merge.sh alongside
fastembed/watchdog); `add_many` falls back to the slow row path with a loud
warning if pyarrow is ever absent.
