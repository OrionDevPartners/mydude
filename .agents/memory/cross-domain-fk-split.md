---
name: Cross-domain FK split
description: Why per-domain physical DBs cannot keep cross-domain foreign keys, and how table creation must handle it.
---

# Cross-domain foreign keys under the per-domain DB split

Once each business domain owns a SEPARATE physical Postgres database, a foreign key
can never span two domains — Postgres cannot enforce a cross-database FK. Any model
whose FK points at a table owned by a different domain (e.g. telephony
`call_sessions.bot_id` and sales `sales_conversations.bot_id` both reference fleet
`bots`) will FAIL at `create_all` with `relation "<x>" does not exist` because the
referenced table is not present in that domain's database.

**Rule:** when creating a domain's table subset, strip FK constraints whose referred
table is NOT in that domain's local table set, then restore them in the in-memory
metadata afterwards (so ORM relationships and other domains' DDL are unaffected).
See `_strip_cross_domain_fks` in `src/database.py`.

**Why:** physical separation is a hard requirement of the task; cross-DB FKs are a
Postgres impossibility, not a bug. Referential integrity for those specific edges
becomes application-enforced.

**How to apply:**
- Adding a new domain-owned table with an FK to another domain's table is fine — the
  strip handles it — but you must validate the reference in application code (the
  DB will not).
- A missing *local* FK target (same domain) still fails loud — that's a real error,
  not a cross-domain edge.
- Data backfill into per-domain DBs must use typed Core inserts (not raw `text()`):
  raw binds can't adapt a Python dict for a JSON/JSONB column ("can't adapt type
  'dict'"). Use `pg_insert(table).on_conflict_do_nothing()` for idempotent re-runs
  and reset integer PK sequences to MAX(id) after copying. See
  `scripts/migrate_domain_dbs.py`.
