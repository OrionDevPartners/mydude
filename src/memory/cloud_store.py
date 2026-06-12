"""
CloudMemoryAdapter — wraps vendored Mem0 store for the cloud side.

The swarm never imports Mem0 directly; it always goes through this adapter.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

from .adapter import MemoryAdapterBase, MemoryEntry

logger = logging.getLogger(__name__)


class CloudMemoryAdapter(MemoryAdapterBase):
    """Mem0-backed cloud memory store (or local-file fallback)."""

    def __init__(self) -> None:
        try:
            from src.vendors.mem0.store import Mem0Store
            self._store = Mem0Store()
            self._available = True
        except Exception as e:
            logger.warning("CloudMemoryAdapter (Mem0) init failed: %s", e)
            self._store = None
            self._available = False

        # Durable DB-backed cache so cloud-side entries survive process restarts
        # even when the Mem0 store is the ephemeral local-file fallback.
        self._cache: Dict[str, MemoryEntry] = {}
        self._restore_cache_from_db()

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        if self._available and self._store:
            try:
                record = self._store.add(
                    content=entry.content,
                    category=entry.category,
                    confidence=entry.confidence,
                    source=entry.source,
                    metadata={
                        "memory_id": entry.memory_id,
                        "verified": entry.verified,
                        "confidence": entry.confidence,
                        "category": entry.category,
                        "source": entry.source,
                        "created_at": entry.created_at,
                        "updated_at": entry.updated_at,
                        "decay": entry.decay,
                    },
                )
                # Always adopt the store-assigned id so the bridge can delete by the
                # correct id on subsequent merge operations.
                if record.memory_id:
                    entry.memory_id = record.memory_id
            except Exception as e:
                logger.warning("CloudMemoryAdapter.add failed: %s", e)

        # Cache + flush to the durable DB store (keyed by the final memory_id).
        self._cache[entry.memory_id] = entry
        try:
            from . import db_store
            db_store.upsert_entry("cloud", entry)
        except Exception as e:
            logger.warning("CloudMemoryAdapter.add DB persist failed: %s", e)
        return entry

    def search(self, query: str, top_k: int = 5,
               category: Optional[str] = None) -> List[MemoryEntry]:
        if not self._available or not self._store:
            return []
        try:
            records = self._store.search(query, top_k=top_k, category=category)
            return [self._record_to_entry(r) for r in records]
        except Exception as e:
            logger.warning("CloudMemoryAdapter.search failed: %s", e)
            return []

    def get_all(self) -> List[MemoryEntry]:
        # Union the live store with the durable DB-backed cache (cache wins on
        # id collisions) so entries persisted before a restart are never lost.
        merged: Dict[str, MemoryEntry] = {}
        if self._available and self._store:
            try:
                for r in self._store.get_all():
                    e = self._record_to_entry(r)
                    merged[e.memory_id] = e
            except Exception as e:
                logger.warning("CloudMemoryAdapter.get_all failed: %s", e)
        for mid, entry in self._cache.items():
            merged[mid] = entry
        return list(merged.values())

    def delete(self, memory_id: str) -> bool:
        deleted = False
        if self._available and self._store:
            try:
                deleted = bool(self._store.delete(memory_id))
            except Exception as e:
                logger.warning("CloudMemoryAdapter.delete failed: %s", e)
        if memory_id in self._cache:
            self._cache.pop(memory_id, None)
            deleted = True
        try:
            from . import db_store
            if db_store.delete_entry("cloud", memory_id):
                deleted = True
        except Exception as e:
            logger.warning("CloudMemoryAdapter.delete DB remove failed: %s", e)
        return deleted

    def _restore_cache_from_db(self) -> None:
        """Load the durable cloud-side entries from the DB on startup."""
        try:
            from . import db_store
            for entry in db_store.load_entries("cloud"):
                self._cache[entry.memory_id] = entry
        except Exception as e:
            logger.warning("CloudMemoryAdapter cache restore from DB failed: %s", e)

    def apply_decay(self) -> None:
        if not self._available or not self._store:
            return
        try:
            self._store.apply_decay()
        except Exception as e:
            logger.warning("CloudMemoryAdapter.apply_decay failed: %s", e)

    def stats(self) -> Dict:
        base = {
            "adapter": "mem0_cloud",
            "available": self._available,
            "cache_entries": len(self._cache),
        }
        if self._available and self._store:
            try:
                base.update(self._store.stats())
            except Exception:
                pass
        return base

    @staticmethod
    def _record_to_entry(record) -> MemoryEntry:
        # Restore verified from metadata — it is written there by add() and must
        # survive the cloud round-trip so the bridge never downgrades a VERIFIED claim.
        meta = record.metadata or {}
        return MemoryEntry(
            memory_id=record.memory_id,
            content=record.content,
            category=record.category,
            confidence=record.confidence,
            source=record.source,
            created_at=record.created_at,
            updated_at=record.updated_at,
            access_count=record.access_count,
            decay=record.decay,
            verified=bool(meta.get("verified", False)),
            metadata=meta,
        )
