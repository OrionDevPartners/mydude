"""Tests for durable, DB-backed persistence of the memory substrate.

The substrate's adapter caches and audit buffer used to be process-local: the
Cognee KG was JSON-persisted and Mem0 had a local-file fallback, but the
in-memory ``_local_cache`` / cloud cache and the audit ring were rebuilt empty
on every restart, so long-term memories accumulated over many tasks were lost on
a redeploy. These tests prove the new DB persistence closes that gap:

  * ``db_store`` round-trips an entry (upsert → load → delete) per adapter side;
  * a fresh ``LocalMemoryAdapter`` / ``CloudMemoryAdapter`` rehydrates its cache
    from the DB on init (simulating a restart);
  * a delete (forget) removes the durable row so the deletion survives a restart;
  * substrate audit events are persisted and a fresh substrate rehydrates them.

The slow KG-ingest path (embedding + JSON save) is bypassed by toggling the
local adapter's availability off, so these tests are fast and hermetic while
still exercising the real DB round-trip.

Runnable two ways:
  * ``python tests/test_memory_persistence.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_memory_persistence.py``
"""
import sys
import time
import uuid

from src.database import init_db, SessionLocal
from src.models import MemoryEntryRecord, MemoryAuditLog
from src.memory import db_store
from src.memory.adapter import MemoryEntry, MemoryEvent, MemoryEventType
from src.memory.local_store import LocalMemoryAdapter
from src.memory.cloud_store import CloudMemoryAdapter
from src.memory.substrate import MemorySubstrate

TEST_SOURCE = "pytest:memory_persistence"


def _cleanup():
    db = SessionLocal()
    try:
        db.query(MemoryEntryRecord).filter(
            MemoryEntryRecord.source == TEST_SOURCE
        ).delete(synchronize_session=False)
        db.query(MemoryAuditLog).filter(
            MemoryAuditLog.detail.like("%pytest-marker-%")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _kg_off(adapter: LocalMemoryAdapter) -> None:
    """Disable the slow KG-ingest path so add() only exercises cache + DB."""
    adapter._available = False
    adapter._query = None
    adapter._graph = None


def test_db_store_roundtrip_per_adapter():
    init_db()
    _cleanup()
    try:
        mid = str(uuid.uuid4())
        for side in ("local", "cloud"):
            entry = MemoryEntry(
                memory_id=mid,
                content="Launch is March 14 2026",
                category="fact",
                confidence=0.9,
                source=TEST_SOURCE,
                verified=True,
                metadata={"goal": "launch"},
            )
            assert db_store.upsert_entry(side, entry) is True
            loaded = [e for e in db_store.load_entries(side) if e.memory_id == mid]
            assert len(loaded) == 1, f"{side}: entry not loaded back"
            got = loaded[0]
            assert got.content == entry.content
            assert got.verified is True
            assert got.confidence == 0.9
            assert got.metadata.get("goal") == "launch"
            # Upsert is idempotent: same (memory_id, adapter) updates, not dupes.
            entry.confidence = 0.5
            db_store.upsert_entry(side, entry)
            again = [e for e in db_store.load_entries(side) if e.memory_id == mid]
            assert len(again) == 1
            assert again[0].confidence == 0.5
            assert db_store.delete_entry(side, mid) is True
            assert not [e for e in db_store.load_entries(side) if e.memory_id == mid]
        print("PASS test_db_store_roundtrip_per_adapter")
    finally:
        _cleanup()


def test_local_adapter_rehydrates_from_db():
    init_db()
    _cleanup()
    try:
        adapter = LocalMemoryAdapter()
        _kg_off(adapter)
        entry = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            content="Local durable claim",
            category="decision",
            confidence=0.8,
            source=TEST_SOURCE,
        )
        adapter.add(entry)

        # Simulate a restart: a fresh adapter must rehydrate from the DB.
        restarted = LocalMemoryAdapter()
        _kg_off(restarted)
        ids = [e.memory_id for e in restarted.get_all()]
        assert entry.memory_id in ids, "local entry did not survive restart"

        # Delete must also be durable.
        assert restarted.delete(entry.memory_id) is True
        again = LocalMemoryAdapter()
        _kg_off(again)
        assert entry.memory_id not in [e.memory_id for e in again.get_all()]
        print("PASS test_local_adapter_rehydrates_from_db")
    finally:
        _cleanup()


def test_cloud_adapter_rehydrates_from_db():
    init_db()
    _cleanup()
    try:
        adapter = CloudMemoryAdapter()
        entry = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            content="Cloud durable claim",
            category="fact",
            confidence=0.7,
            source=TEST_SOURCE,
        )
        returned = adapter.add(entry)
        # add() may adopt a store-assigned id; that final id is what persists.
        final_id = returned.memory_id

        restarted = CloudMemoryAdapter()
        ids = [e.memory_id for e in restarted.get_all()]
        assert final_id in ids, "cloud entry did not survive restart"

        assert restarted.delete(final_id) is True
        again = CloudMemoryAdapter()
        assert final_id not in [e.memory_id for e in again.get_all()]
        print("PASS test_cloud_adapter_rehydrates_from_db")
    finally:
        _cleanup()


def test_audit_events_persist_and_rehydrate():
    init_db()
    _cleanup()
    try:
        marker = f"pytest-marker-{uuid.uuid4().hex[:8]}"
        ev = MemoryEvent(
            event_type=MemoryEventType.PERSIST,
            detail=f"{marker}: durable audit",
            memory_ids=["a", "b"],
            timestamp=time.time(),
        )
        assert db_store.append_audit_event(ev) is True
        loaded = [e for e in db_store.load_audit_events(limit=500)
                  if marker in e.detail]
        assert len(loaded) == 1
        assert loaded[0].event_type == MemoryEventType.PERSIST
        assert loaded[0].memory_ids == ["a", "b"]

        # A fresh substrate rehydrates the audit ring from the DB.
        sub = MemorySubstrate()
        details = [e["detail"] for e in sub.audit_events(limit=500)]
        assert any(marker in d for d in details), "audit event not rehydrated"
        print("PASS test_audit_events_persist_and_rehydrate")
    finally:
        _cleanup()


if __name__ == "__main__":
    failures = 0
    for fn in (
        test_db_store_roundtrip_per_adapter,
        test_local_adapter_rehydrates_from_db,
        test_cloud_adapter_rehydrates_from_db,
        test_audit_events_persist_and_rehydrate,
    ):
        try:
            fn()
        except Exception as exc:  # pragma: no cover
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    sys.exit(1 if failures else 0)
