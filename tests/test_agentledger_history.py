"""Tests for the Agent Ledger's lasting rebuild history (Task #92).

The seeder rebuilds the ledger from real project state on every run, but the
append-only ``ledger_events`` audit log must SURVIVE each rebuild so a history of
reseeds accumulates over merges. These tests prove:

  * init_ledger(drop=True, preserve=["ledger_events"]) keeps the audit table (and
    its rows) while every OTHER table is dropped and recreated empty.
  * seed() accumulates one new seed event per run AND still fully rebuilds the
    non-audit ledger fresh each time (identical counts, no duplication/stale rows).
  * query.events()/summary() expose the rebuild history (newest-first, with the
    per-rebuild stats parsed from payload_json).

Hermetic: the ledger is pointed at a throwaway SQLite file via AGENT_LEDGER_URL
BEFORE importing agentledger (the engine binds at import), so the real
agent_ledger.db is never touched. seed() reads only the repo's manifests/source
(no network, no app database).

Runnable two ways:
  * python tests/test_agentledger_history.py   (standalone; exits non-zero on failure)
  * pytest tests/test_agentledger_history.py    (test_* functions; no plugins needed)
"""
import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolate to a throwaway DB BEFORE importing agentledger (engine binds at import).
_TMP_DIR = tempfile.mkdtemp(prefix="agentledger_test_")
_TMP_DB = os.path.join(_TMP_DIR, "ledger.db")
os.environ["AGENT_LEDGER_URL"] = f"sqlite:///{_TMP_DB}"

from agentledger import query  # noqa: E402
from agentledger.db import SessionLocal, engine, init_ledger  # noqa: E402
from agentledger.models import LedgerEvent, Package  # noqa: E402
from agentledger.seed import seed  # noqa: E402


def _cleanup():
    try:
        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


atexit.register(_cleanup)


def test_init_ledger_preserves_only_named_table():
    """A preserve=[audit] rebuild keeps the audit rows but wipes everything else."""
    seed()  # populate every table incl. packages + a seed event
    before_events = query.summary()["rebuild_events"]
    assert before_events >= 1, "expected at least one seed event after seeding"

    init_ledger(drop=True, preserve=[LedgerEvent.__tablename__])

    db = SessionLocal()
    try:
        kept = db.query(LedgerEvent).count()
        pkgs = db.query(Package).count()
    finally:
        db.close()
    assert kept == before_events, f"audit rows not preserved: {kept} vs {before_events}"
    assert pkgs == 0, f"non-audit table not rebuilt empty: {pkgs} package rows remained"


def test_seed_accumulates_history_and_rebuilds_fresh():
    """Each reseed adds exactly one audit row while non-audit counts stay stable."""
    s1 = seed()
    n1 = query.summary()["rebuild_events"]
    s2 = seed()
    n2 = query.summary()["rebuild_events"]

    assert n2 == n1 + 1, f"rebuild_events should grow by 1 per reseed, got {n1} -> {n2}"
    # Non-audit ledger is fully rebuilt from the same source each run: counts must
    # be identical (proves no stale rows survived and no duplication occurred).
    assert s1 == s2, f"non-audit stats drifted/duplicated across reseed: {s1} vs {s2}"


def test_events_query_returns_history_with_stats():
    """events()/CLI expose the rebuild log newest-first with parsed per-run stats."""
    seed()
    rows = query.events(action="seed")
    assert len(rows) >= 1, "expected at least one seed event"

    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True), "events must be newest-first"

    top = rows[0]
    assert top["action"] == "seed"
    assert isinstance(top["stats"], dict), "per-rebuild stats must be parsed from payload_json"
    assert top["stats"].get("packages", 0) > 0, "stats should carry real package counts"
    assert top["stats"].get("functions", 0) > 0, "stats should carry real function counts"

    # The limit is honoured and never returns more than requested.
    assert len(query.events(limit=1, action="seed")) == 1


_TESTS = [
    test_init_ledger_preserves_only_named_table,
    test_seed_accumulates_history_and_rebuilds_fresh,
    test_events_query_returns_history_with_stats,
]


def main():
    # Start from a clean, fully-empty ledger (no preserved rows) for determinism.
    init_ledger(drop=True)
    failures = []
    try:
        for t in _TESTS:
            try:
                t()
                print("PASS", t.__name__, flush=True)
            except Exception as e:
                import traceback
                print("FAIL", t.__name__, "->", e, flush=True)
                traceback.print_exc()
                failures.append(t.__name__)
    finally:
        _cleanup()
    print("\n%d passed, %d failed" % (len(_TESTS) - len(failures), len(failures)), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
