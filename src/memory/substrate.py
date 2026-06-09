"""
MemorySubstrate — the unified memory API the swarm calls.

Single entry point for all memory operations:
  - write_claim()      persist a VERIFIED/load-bearing claim into long-term memory
  - recall()           retrieve semantically related prior memories
  - consolidate()      promote high-confidence claims, apply decay
  - sync()             run the bidirectional Cognee↔Mem0 bridge
  - find_contradictions()  semantic contradiction check (replaces Jaccard)
  - audit_events()     return recent audit events for dashboard surfacing

Thread-safe singleton: get_substrate() returns the shared instance.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .adapter import MemoryEntry, MemoryEvent, MemoryEventType
from .local_store import LocalMemoryAdapter
from .cloud_store import CloudMemoryAdapter
from .bridge import MemoryBridge, SyncReport

logger = logging.getLogger(__name__)

_SUBSTRATE: Optional["MemorySubstrate"] = None
_SUBSTRATE_LOCK = threading.Lock()

_AUDIT_MAXLEN = 200


class MemorySubstrate:
    """
    Unified memory substrate.  Callers use this; they never touch Cognee
    or Mem0 directly.
    """

    def __init__(self) -> None:
        self._local = LocalMemoryAdapter()
        self._cloud = CloudMemoryAdapter()
        self._bridge = MemoryBridge(self._local, self._cloud)
        self._audit: Deque[MemoryEvent] = deque(maxlen=_AUDIT_MAXLEN)
        self._last_sync: Optional[float] = None
        self._lock = threading.Lock()

    def write_claim(
        self,
        content: str,
        category: str = "fact",
        confidence: float = 1.0,
        source: str = "",
        verified: bool = False,
        metadata: Optional[Dict] = None,
    ) -> MemoryEntry:
        """Persist a claim/fact into long-term memory (local KG + cloud)."""
        entry = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            content=content,
            category=category,
            confidence=confidence,
            source=source,
            verified=verified,
            metadata=metadata or {},
        )
        with self._lock:
            # Cloud.add may reassign entry.memory_id to a cloud-assigned id.
            # By calling cloud first, local.add then uses the final stable id,
            # keeping both stores keyed by the same memory_id.
            self._cloud.add(entry)
            self._local.add(entry)

        event = MemoryEvent(
            event_type=MemoryEventType.PERSIST,
            detail=f"Persisted [{category}] (conf={confidence:.2f}, verified={verified}): {content[:100]}",
            memory_ids=[entry.memory_id],
        )
        self._audit.append(event)
        logger.info(event.to_log_str())
        return entry

    def recall(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
        min_confidence: float = 0.3,
    ) -> List[MemoryEntry]:
        """Recall semantically related memories for a given query."""
        results: List[MemoryEntry] = []
        try:
            local = self._local.search(query, top_k=top_k, category=category)
            results.extend(local)
        except Exception as e:
            logger.warning("Recall local search failed: %s", e)

        try:
            if len(results) < top_k:
                cloud = self._cloud.search(query, top_k=top_k - len(results), category=category)
                seen = {e.memory_id for e in results}
                for e in cloud:
                    if e.memory_id not in seen:
                        results.append(e)
        except Exception as e:
            logger.warning("Recall cloud search failed: %s", e)

        results = [e for e in results if e.confidence >= min_confidence]
        results = results[:top_k]

        if results:
            event = MemoryEvent(
                event_type=MemoryEventType.RECALL,
                detail=f"Recalled {len(results)} memories for query: {query[:80]}",
                memory_ids=[e.memory_id for e in results],
            )
            self._audit.append(event)
            logger.info(event.to_log_str())

        return results

    def find_contradictions(
        self,
        claim: str,
        threshold: float = 0.25,
    ) -> List[Dict]:
        """
        Semantic contradiction check using the local KG.
        Falls back to an empty list on error (never crashes the swarm).
        """
        try:
            return self._local.find_contradictions(claim, threshold=threshold)
        except Exception as e:
            logger.warning("find_contradictions failed: %s", e)
            return []

    def consolidate(
        self,
        min_confidence: float = 0.75,
        decay_rate: float = 0.01,
    ) -> int:
        """
        Promote high-confidence VERIFIED claims; apply memory decay.
        Returns the number of entries promoted.
        """
        promoted = 0
        try:
            with self._lock:
                local_entries = self._local.get_all()
                for entry in local_entries:
                    if entry.confidence >= min_confidence and not entry.verified:
                        entry.verified = True
                        self._local.add(entry)
                        promoted += 1
                self._local.apply_decay()
                self._cloud.apply_decay()
        except Exception as e:
            logger.warning("consolidate failed: %s", e)

        event = MemoryEvent(
            event_type=MemoryEventType.CONSOLIDATE,
            detail=f"Consolidated memory: promoted={promoted} entries (min_conf={min_confidence})",
        )
        self._audit.append(event)
        logger.info(event.to_log_str())
        return promoted

    def sync(self, direction: str = "both", min_confidence: float = 0.5) -> SyncReport:
        """Run the bidirectional Cognee↔Mem0 bridge."""
        report = self._bridge.sync(direction=direction, min_confidence=min_confidence)  # type: ignore[arg-type]
        self._last_sync = time.time()
        for ev in report.events:
            self._audit.append(ev)
        return report

    def persist_handoff(
        self,
        goal: str,
        facts: List[str],
        decisions: List[str],
        claim_ledger_summary: str = "",
        session_id: str = "",
    ) -> List[MemoryEntry]:
        """
        Called at task end: persist the load-bearing facts/decisions from the
        Handoff into long-term memory, keyed by goal topic.
        """
        persisted: List[MemoryEntry] = []
        source = f"handoff:{session_id}" if session_id else "handoff"

        for fact in facts[:10]:
            if len(fact) > 20:
                e = self.write_claim(
                    content=fact[:500],
                    category="fact",
                    confidence=0.8,
                    source=source,
                    metadata={"goal": goal[:100]},
                )
                persisted.append(e)

        for decision in decisions[:8]:
            if len(decision) > 20:
                e = self.write_claim(
                    content=decision[:500],
                    category="decision",
                    confidence=0.9,
                    source=source,
                    metadata={"goal": goal[:100]},
                )
                persisted.append(e)

        if claim_ledger_summary:
            e = self.write_claim(
                content=claim_ledger_summary[:1000],
                category="claim_ledger",
                confidence=0.85,
                source=source,
                metadata={"goal": goal[:100]},
            )
            persisted.append(e)

        logger.info(
            "[MEMORY:PERSIST] Persisted %d entries from handoff for goal: %s",
            len(persisted),
            goal[:80],
        )
        return persisted

    def inject_for_task(self, goal: str, top_k: int = 5) -> List[str]:
        """
        Called at task start: recall related prior memories and return them
        as formatted strings the orchestrator can inject into the first Handoff.
        """
        memories = self.recall(goal, top_k=top_k, min_confidence=0.4)
        if not memories:
            return []
        injected = []
        for m in memories:
            tag = "[VERIFIED]" if m.verified else "[RECALLED]"
            injected.append(
                f"{tag} ({m.category}, conf={m.confidence:.2f}): {m.content[:200]}"
            )
        logger.info(
            "[MEMORY:RECALL] Injected %d prior memories for goal: %s",
            len(injected),
            goal[:80],
        )
        return injected

    def audit_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent memory audit events for dashboard surfacing."""
        events = list(self._audit)[-limit:]
        return [
            {
                "type": e.event_type.value,
                "detail": e.detail,
                "memory_ids": e.memory_ids[:3],
                "timestamp": e.timestamp,
                "log": e.to_log_str(),
            }
            for e in reversed(events)
        ]

    def status(self) -> Dict[str, Any]:
        """Return a summary for dashboard display."""
        return {
            "local": self._local.stats(),
            "cloud": self._cloud.stats(),
            "last_sync": self._last_sync,
            "audit_events": len(self._audit),
        }


def get_substrate() -> MemorySubstrate:
    """Return the process-wide shared MemorySubstrate (lazy init)."""
    global _SUBSTRATE
    if _SUBSTRATE is None:
        with _SUBSTRATE_LOCK:
            if _SUBSTRATE is None:
                _SUBSTRATE = MemorySubstrate()
    return _SUBSTRATE


# Type alias for compatibility
from typing import Any  # noqa: E402
