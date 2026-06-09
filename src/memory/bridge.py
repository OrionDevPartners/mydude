"""
Memory bridge — bidirectional sync between local (Cognee) and cloud (Mem0).

Sync semantics:
- Deduplication: entries with identical content (normalized) are merged.
- Merge rule: last-writer-wins on `updated_at`, except VERIFIED entries
  (entry.verified=True) are NEVER overwritten by a lower-confidence record.
- Idempotent: repeated runs converge; no entries are duplicated.
- Direction: 'local→cloud', 'cloud→local', or 'both'.

Adapted from Mem0's sync bridge design (Apache-2.0).
"""

from __future__ import annotations

import copy
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from .adapter import MemoryAdapterBase, MemoryEntry, MemoryEvent, MemoryEventType

logger = logging.getLogger(__name__)

SyncDirection = Literal["local→cloud", "cloud→local", "both"]


@dataclass
class SyncReport:
    direction: str
    pushed: int = 0
    pulled: int = 0
    merged: int = 0
    skipped: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    events: List[MemoryEvent] = field(default_factory=list)

    def finish(self) -> "SyncReport":
        self.finished_at = time.time()
        return self

    def summary(self) -> str:
        elapsed = round(self.finished_at - self.started_at, 2)
        return (
            f"Sync({self.direction}): pushed={self.pushed} pulled={self.pulled} "
            f"merged={self.merged} skipped={self.skipped} errors={self.errors} "
            f"in {elapsed}s"
        )


def _content_hash(content: str) -> str:
    normalized = " ".join(content.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _merge_entries(existing: MemoryEntry, incoming: MemoryEntry) -> MemoryEntry:
    """Merge two entries; VERIFIED status is never lost from either side.

    Merge rules:
    - VERIFIED is union (if either is VERIFIED, result is VERIFIED).
    - Content/metadata use LWW (last-writer-wins on updated_at).
    - Confidence takes the max; decay takes the max; access_count accumulates.
    """
    will_be_verified = existing.verified or incoming.verified
    shared = {
        "memory_id": existing.memory_id,
        "confidence": max(existing.confidence, incoming.confidence),
        "created_at": min(existing.created_at, incoming.created_at),
        "access_count": existing.access_count + incoming.access_count,
        "decay": max(existing.decay, incoming.decay),
        "verified": will_be_verified,
        "metadata": {**existing.metadata, **incoming.metadata},
    }

    if incoming.updated_at >= existing.updated_at:
        return MemoryEntry(
            content=incoming.content,
            category=incoming.category,
            source=incoming.source or existing.source,
            updated_at=incoming.updated_at,
            **shared,
        )
    else:
        # existing wins on timestamp; preserve VERIFIED from incoming if needed
        return MemoryEntry(
            content=existing.content,
            category=existing.category,
            source=existing.source,
            updated_at=existing.updated_at,
            **shared,
        )


class MemoryBridge:
    """
    Bidirectional sync bridge between a local and a cloud memory adapter.
    """

    def __init__(
        self,
        local: MemoryAdapterBase,
        cloud: MemoryAdapterBase,
    ) -> None:
        self._local = local
        self._cloud = cloud

    def sync(
        self,
        direction: SyncDirection = "both",
        min_confidence: float = 0.5,
    ) -> SyncReport:
        report = SyncReport(direction=direction)
        try:
            if direction in ("local→cloud", "both"):
                self._push(report, min_confidence)
            if direction in ("cloud→local", "both"):
                self._pull(report, min_confidence)
        except Exception as e:
            report.errors += 1
            logger.error("MemoryBridge.sync failed: %s", e)
        finally:
            report.finish()

        event = MemoryEvent(
            event_type=MemoryEventType.SYNC,
            detail=report.summary(),
        )
        report.events.append(event)
        logger.info(event.to_log_str())
        return report

    @staticmethod
    def _has_changed(merged: MemoryEntry, existing: MemoryEntry) -> bool:
        """Return True if merged differs from existing in any meaningful way."""
        return (
            merged.updated_at > existing.updated_at
            or merged.verified != existing.verified
            or merged.confidence > existing.confidence + 1e-6
        )

    def _push(self, report: SyncReport, min_confidence: float) -> None:
        local_entries = self._local.get_all()
        cloud_entries = self._cloud.get_all()
        # Build cloud hash map and keep it updated during the loop so repeated
        # content hashes across multiple local entries don't create duplicates.
        cloud_hashes: Dict[str, MemoryEntry] = {
            _content_hash(e.content): e for e in cloud_entries
        }

        for entry in local_entries:
            if entry.confidence < min_confidence:
                report.skipped += 1
                continue
            h = _content_hash(entry.content)
            if h in cloud_hashes:
                existing = cloud_hashes[h]
                merged = _merge_entries(existing, entry)
                if self._has_changed(merged, existing):
                    try:
                        self._cloud.delete(existing.memory_id)
                        self._cloud.add(merged)
                        # Update hash map so subsequent entries see the merged version
                        cloud_hashes[h] = merged
                        report.merged += 1
                    except Exception as e:
                        logger.warning("Push merge failed: %s", e)
                        report.errors += 1
                else:
                    report.skipped += 1
            else:
                try:
                    # Use a shallow copy so cloud.add() cannot mutate the live
                    # _local_cache entry in-place (cloud assigns a new memory_id
                    # which would leave the local cache keyed by the stale old id).
                    cloud_copy = copy.copy(entry)
                    self._cloud.add(cloud_copy)
                    # Register the copy (with cloud id) in the hash map to
                    # deduplicate later local entries in this same push run.
                    cloud_hashes[h] = cloud_copy
                    report.pushed += 1
                except Exception as e:
                    logger.warning("Push add failed: %s", e)
                    report.errors += 1

    def _pull(self, report: SyncReport, min_confidence: float) -> None:
        cloud_entries = self._cloud.get_all()
        local_entries = self._local.get_all()
        # Build local hash map and keep it updated during the loop to prevent
        # duplicate pulls when multiple cloud entries share normalized content.
        local_hashes: Dict[str, MemoryEntry] = {
            _content_hash(e.content): e for e in local_entries
        }

        for entry in cloud_entries:
            if entry.confidence < min_confidence:
                report.skipped += 1
                continue
            h = _content_hash(entry.content)
            if h in local_hashes:
                existing = local_hashes[h]
                merged = _merge_entries(existing, entry)
                # Use full change check (timestamp, verified, confidence) —
                # not timestamp-only, so VERIFIED gains with equal ts are applied.
                if self._has_changed(merged, existing):
                    try:
                        self._local.delete(existing.memory_id)
                        self._local.add(merged)
                        # Update hash map to reflect merged state
                        local_hashes[h] = merged
                        report.merged += 1
                    except Exception as e:
                        logger.warning("Pull merge failed: %s", e)
                        report.errors += 1
                else:
                    report.skipped += 1
            else:
                try:
                    self._local.add(entry)
                    # Register to prevent duplicate pulls
                    local_hashes[h] = entry
                    report.pulled += 1
                except Exception as e:
                    logger.warning("Pull add failed: %s", e)
                    report.errors += 1
