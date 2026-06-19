---
name: Governance enactment setting writes must be race-safe
description: Why AppSetting writes in governance enactment use a SAVEPOINT insert-or-update, not a plain query->add get-or-create.
---

# Governance enactment AppSetting writes must be insert-or-update under a SAVEPOINT

When an enacted governance proposal applies a bounded setting change, the write
to the `app_settings` table must be an idempotent insert-or-update guarded by a
SAVEPOINT (`session.begin_nested()` + `flush()`; on `IntegrityError`, re-read and
update). A naive `query(...).first()` then `db.add(AppSetting(...))` get-or-create
is racy.

**Why:** `tests/run_all.py` runs all suites in parallel (multiple workers)
against a SINGLE shared Postgres dev database. Several governance suites enact
proposals that map to the SAME `swarm.*` setting key (e.g. `swarm.min_cs_threshold`
from "compliance correction" wording, also written by the tuning suite). With
get-or-create, two workers both see "no row", both INSERT, and the second commit
dies with `psycopg2.errors.UniqueViolation` on `ix_app_settings_key`. That
exception surfaces at `operator_enact`'s `db.commit()`, gets caught, rolled back,
and `operator_enact` returns False — so the enactment silently fails and the test
asserting `is True` fails. The same race can bite production whenever two
enactments touch the same key concurrently.

**How to apply:** Any place that persists a governance/enactment setting (or any
unique-keyed row that concurrent code paths may create) should go through the
race-safe `_write_setting` helper pattern, not an inline get-or-create. Do not
"fix" this by serializing tests or deleting the key first in the test — the fix
belongs in the write path so it is correct under real concurrency.
