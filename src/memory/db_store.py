"""
DB-backed durable persistence for the memory substrate.

The local (Cognee) and cloud (Mem0) adapters keep in-process caches; the Cognee
KG is JSON-persisted to disk and Mem0 has a local-file fallback, but those are
process/host-local and are lost on redeploy. This module persists every
``MemoryEntry`` to PostgreSQL (``memory_entries``) and every ``MemoryEvent`` to
``memory_audit_logs`` so long-term memory and its audit trail are truly durable
across restarts.

All functions degrade safely: if the DB is unavailable they log loudly and fall
back to the in-memory caches rather than crashing the swarm. Nothing here ever
fabricates data — a failed load returns nothing and a failed write is logged.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from .adapter import MemoryEntry, MemoryEvent, MemoryEventType

logger = logging.getLogger(__name__)


def _session():
    """Open a DB session, or return None if the DB layer is unavailable."""
    try:
        from src.database import SessionLocal
        return SessionLocal()
    except Exception as e:  # pragma: no cover - only when DATABASE_URL absent
        logger.warning("memory db_store: SessionLocal unavailable: %s", e)
        return None


def _record_to_entry(row) -> MemoryEntry:
    try:
        meta = json.loads(row.metadata_json) if row.metadata_json else {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return MemoryEntry(
        memory_id=row.memory_id,
        content=row.content,
        category=row.category or "fact",
        confidence=row.confidence if row.confidence is not None else 1.0,
        source=row.source or "",
        created_at=row.entry_created_at if row.entry_created_at is not None else 0.0,
        updated_at=row.entry_updated_at if row.entry_updated_at is not None else 0.0,
        access_count=row.access_count or 0,
        decay=row.decay if row.decay is not None else 1.0,
        verified=bool(row.verified),
        metadata=meta,
    )


def load_entries(adapter: str) -> List[MemoryEntry]:
    """Load all persisted entries for one adapter side ("local" | "cloud")."""
    db = _session()
    if db is None:
        return []
    try:
        from src.models import MemoryEntryRecord
        rows = (
            db.query(MemoryEntryRecord)
            .filter(MemoryEntryRecord.adapter == adapter)
            .all()
        )
        return [_record_to_entry(r) for r in rows]
    except Exception as e:
        logger.warning("memory db_store.load_entries(%s) failed: %s", adapter, e)
        return []
    finally:
        db.close()


def search_entries(
    adapter: Optional[str] = None,
    q: Optional[str] = None,
    category: Optional[str] = None,
    after_ts: Optional[float] = None,
    before_ts: Optional[float] = None,
    page: int = 1,
    per_page: int = 25,
) -> dict:
    """Server-side search/filter/pagination over persisted memory entries.

    Filters (all optional, combined with AND):
      - ``adapter``  : "local" | "cloud" side that wrote the row
      - ``q``        : case-insensitive substring over content + source
      - ``category`` : exact category match
      - ``after_ts`` : only entries created at/after this epoch second
      - ``before_ts``: only entries created at/before this epoch second

    Pagination is 1-based; ``per_page`` is clamped to [1, 200]. Newest entries
    (by ``entry_created_at``) come first. Returns a dict with the page of
    ``entries`` plus ``total`` (matching the filters), ``page``, ``per_page``,
    ``total_pages``, and the distinct ``categories``/``adapters`` present across
    ALL rows (so filter dropdowns stay stable regardless of the active filter).

    Degrades safely: an unavailable DB returns an empty, well-formed result.
    """
    try:
        page = max(1, int(page))
    except Exception:
        page = 1
    try:
        per_page = int(per_page)
    except Exception:
        per_page = 25
    per_page = max(1, min(200, per_page))

    empty = {
        "entries": [],
        "total": 0,
        "page": page,
        "per_page": per_page,
        "total_pages": 1,
        "categories": [],
        "adapters": [],
    }

    db = _session()
    if db is None:
        return empty
    try:
        from src.models import MemoryEntryRecord
        query = db.query(MemoryEntryRecord)
        if adapter:
            query = query.filter(MemoryEntryRecord.adapter == adapter)
        if category:
            query = query.filter(MemoryEntryRecord.category == category)
        if q:
            like = f"%{q}%"
            query = query.filter(
                MemoryEntryRecord.content.ilike(like)
                | MemoryEntryRecord.source.ilike(like)
            )
        if after_ts is not None:
            query = query.filter(MemoryEntryRecord.entry_created_at >= after_ts)
        if before_ts is not None:
            query = query.filter(MemoryEntryRecord.entry_created_at <= before_ts)

        total = query.count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = (
            query.order_by(
                MemoryEntryRecord.entry_created_at.desc().nullslast(),
                MemoryEntryRecord.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        categories = [
            r[0]
            for r in db.query(MemoryEntryRecord.category).distinct().all()
            if r[0]
        ]
        adapters = [
            r[0]
            for r in db.query(MemoryEntryRecord.adapter).distinct().all()
            if r[0]
        ]
        entries = []
        for r in rows:
            entry = _record_to_entry(r)
            setattr(entry, "adapter", r.adapter)
            entries.append(entry)
        return {
            "entries": entries,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "categories": sorted(categories),
            "adapters": sorted(adapters),
        }
    except Exception as e:
        logger.warning("memory db_store.search_entries failed: %s", e)
        return empty
    finally:
        db.close()


def upsert_entry(adapter: str, entry: MemoryEntry) -> bool:
    """Insert or update one entry row for the given adapter side."""
    if not entry or not entry.memory_id:
        return False
    db = _session()
    if db is None:
        return False
    try:
        from src.models import MemoryEntryRecord
        try:
            meta_json = json.dumps(entry.metadata or {})
        except Exception:
            meta_json = "{}"
        row = (
            db.query(MemoryEntryRecord)
            .filter(
                MemoryEntryRecord.adapter == adapter,
                MemoryEntryRecord.memory_id == entry.memory_id,
            )
            .first()
        )
        if row is None:
            row = MemoryEntryRecord(memory_id=entry.memory_id, adapter=adapter)
            db.add(row)
        row.content = entry.content
        row.category = entry.category
        row.confidence = entry.confidence
        row.source = entry.source
        row.entry_created_at = entry.created_at
        row.entry_updated_at = entry.updated_at
        row.access_count = entry.access_count
        row.decay = entry.decay
        row.verified = bool(entry.verified)
        row.metadata_json = meta_json
        db.commit()
        return True
    except Exception as e:
        logger.warning(
            "memory db_store.upsert_entry(%s, %s) failed: %s",
            adapter, entry.memory_id, e,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def delete_entry(adapter: str, memory_id: str) -> bool:
    """Delete one entry row for the given adapter side. Returns True if a row
    was removed."""
    if not memory_id:
        return False
    db = _session()
    if db is None:
        return False
    try:
        from src.models import MemoryEntryRecord
        deleted = (
            db.query(MemoryEntryRecord)
            .filter(
                MemoryEntryRecord.adapter == adapter,
                MemoryEntryRecord.memory_id == memory_id,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return bool(deleted)
    except Exception as e:
        logger.warning(
            "memory db_store.delete_entry(%s, %s) failed: %s",
            adapter, memory_id, e,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def append_audit_event(event: MemoryEvent) -> bool:
    """Persist one MemoryEvent to the durable audit log."""
    db = _session()
    if db is None:
        return False
    try:
        from src.models import MemoryAuditLog
        try:
            ids_json = json.dumps(list(event.memory_ids or []))
        except Exception:
            ids_json = "[]"
        db.add(MemoryAuditLog(
            event_type=event.event_type.value,
            detail=event.detail,
            memory_ids_json=ids_json,
            event_ts=event.timestamp,
        ))
        db.commit()
        return True
    except Exception as e:
        logger.warning("memory db_store.append_audit_event failed: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def load_audit_events(limit: int = 200) -> List[MemoryEvent]:
    """Load the most recent persisted audit events, oldest-first."""
    db = _session()
    if db is None:
        return []
    try:
        from src.models import MemoryAuditLog
        rows = (
            db.query(MemoryAuditLog)
            .order_by(MemoryAuditLog.id.desc())
            .limit(limit)
            .all()
        )
        rows = list(reversed(rows))
        events: List[MemoryEvent] = []
        for r in rows:
            try:
                ids = json.loads(r.memory_ids_json) if r.memory_ids_json else []
                if not isinstance(ids, list):
                    ids = []
            except Exception:
                ids = []
            try:
                etype = MemoryEventType(r.event_type)
            except Exception:
                etype = MemoryEventType.PERSIST
            events.append(MemoryEvent(
                event_type=etype,
                detail=r.detail or "",
                memory_ids=ids,
                timestamp=r.event_ts if r.event_ts is not None else 0.0,
            ))
        return events
    except Exception as e:
        logger.warning("memory db_store.load_audit_events failed: %s", e)
        return []
    finally:
        db.close()
