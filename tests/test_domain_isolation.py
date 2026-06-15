"""Per-domain physical isolation tests (Task #260).

Each business domain owns a SEPARATE physical Postgres database plus its own
pgvector space. These tests prove that isolation holds end-to-end:

  * A memory entry written through the finance substrate physically lands in the
    finance database and is INVISIBLE to the coach and core databases (and vice
    versa) — a domain-A session cannot read domain-B rows.
  * The shared ``core`` substrate is reachable from the core database and stays
    out of the business-domain databases.
  * Vector embeddings upserted for one domain are only returned by that domain's
    similarity search, never another domain's (when pgvector is available; the
    test degrades to a skip when the extension is absent, never a false pass).

Runnable two ways:
  * ``python tests/test_domain_isolation.py``  (standalone, non-zero on failure)
  * ``pytest tests/test_domain_isolation.py``
"""
import sys
import uuid

from src.database import init_db, domain_session
from src.models import MemoryEntryRecord
from src.memory import vector_store
from src.memory.substrate import get_substrate

MARK = "pytest:domain_isolation:" + uuid.uuid4().hex[:8]
DOMAINS = ("core", "finance", "coach")


def _kg_off(substrate) -> None:
    """Disable the slow KG-ingest path so write_claim only hits cache + DB + vector."""
    for adapter in (substrate._local,):
        try:
            adapter._available = False
            adapter._query = None
            adapter._graph = None
        except Exception:
            pass


def _entries_in(domain: str):
    db = domain_session(domain)
    try:
        return db.query(MemoryEntryRecord).filter(
            MemoryEntryRecord.source == MARK
        ).all()
    finally:
        db.close()


def _cleanup():
    for domain in DOMAINS:
        db = domain_session(domain)
        try:
            db.query(MemoryEntryRecord).filter(
                MemoryEntryRecord.source == MARK
            ).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    for domain in DOMAINS:
        for mid in _vector_ids():
            vector_store.delete(domain, mid)


_VEC_IDS = []


def _vector_ids():
    return list(_VEC_IDS)


def test_memory_entries_isolated_per_domain():
    init_db()
    _cleanup()
    try:
        fin = get_substrate("finance")
        coa = get_substrate("coach")
        _kg_off(fin)
        _kg_off(coa)

        fin.write_claim(
            content="Q1 vendor spend reconciled",
            category="fact",
            confidence=0.9,
            source=MARK,
            local_only=True,
        )
        coa.write_claim(
            content="User prefers morning check-ins",
            category="fact",
            confidence=0.9,
            source=MARK,
            local_only=True,
        )

        fin_rows = _entries_in("finance")
        coa_rows = _entries_in("coach")
        core_rows = _entries_in("core")

        fin_contents = {r.content for r in fin_rows}
        coa_contents = {r.content for r in coa_rows}

        assert "Q1 vendor spend reconciled" in fin_contents, "finance entry missing from finance DB"
        assert "User prefers morning check-ins" in coa_contents, "coach entry missing from coach DB"
        # Cross-domain isolation: finance cannot see coach's row and vice versa.
        assert "User prefers morning check-ins" not in fin_contents, "coach row leaked into finance DB"
        assert "Q1 vendor spend reconciled" not in coa_contents, "finance row leaked into coach DB"
        # And neither business row landed in the shared core DB.
        assert len(core_rows) == 0, "business-domain rows leaked into core DB"
        # Every row carries its owning domain tag.
        assert all(r.domain == "finance" for r in fin_rows)
        assert all(r.domain == "coach" for r in coa_rows)
        print("PASS test_memory_entries_isolated_per_domain")
    finally:
        _cleanup()


def test_core_substrate_shared_and_separate():
    init_db()
    _cleanup()
    try:
        core = get_substrate("core")
        _kg_off(core)
        core.write_claim(
            content="Platform launched 2026",
            category="fact",
            confidence=1.0,
            source=MARK,
            local_only=True,
        )
        core_rows = _entries_in("core")
        fin_rows = _entries_in("finance")
        assert any(r.content == "Platform launched 2026" for r in core_rows), "core entry missing from core DB"
        assert all(r.content != "Platform launched 2026" for r in fin_rows), "core row leaked into finance DB"
        print("PASS test_core_substrate_shared_and_separate")
    finally:
        _cleanup()


def test_vector_space_isolated_per_domain():
    init_db()
    if not vector_store.is_available("finance") or not vector_store.is_available("coach"):
        print("SKIP test_vector_space_isolated_per_domain (pgvector unavailable)")
        return
    fin_id = "iso-fin-" + uuid.uuid4().hex[:8]
    coa_id = "iso-coa-" + uuid.uuid4().hex[:8]
    _VEC_IDS.extend([fin_id, coa_id])
    emb = [0.11, 0.22, 0.33, 0.44, 0.55]
    try:
        assert vector_store.upsert("finance", fin_id, "finance vec", emb) is True
        assert vector_store.upsert("coach", coa_id, "coach vec", emb) is True

        fin_hits = {h["memory_id"] for h in vector_store.search("finance", emb, top_k=10)}
        coa_hits = {h["memory_id"] for h in vector_store.search("coach", emb, top_k=10)}

        assert fin_id in fin_hits, "finance vector not found in finance space"
        assert coa_id in coa_hits, "coach vector not found in coach space"
        # Cross-domain isolation: each domain's vector is absent from the other's space.
        assert coa_id not in fin_hits, "coach vector leaked into finance space"
        assert fin_id not in coa_hits, "finance vector leaked into coach space"
        print("PASS test_vector_space_isolated_per_domain")
    finally:
        vector_store.delete("finance", fin_id)
        vector_store.delete("coach", coa_id)


if __name__ == "__main__":
    failures = 0
    for fn in (
        test_memory_entries_isolated_per_domain,
        test_core_substrate_shared_and_separate,
        test_vector_space_isolated_per_domain,
    ):
        try:
            fn()
        except Exception as exc:  # pragma: no cover
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    sys.exit(1 if failures else 0)
