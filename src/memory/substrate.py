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

class _NullCloudAdapter:
    """No-op cloud memory adapter used when no permitted cloud knowledge_store
    backend was resolved by the capability registry.

    This satisfies the MemoryBridge interface so the substrate can operate in
    local-only mode without silently reintroducing a blocked cloud backend.
    All writes/reads are no-ops; sync operations are skipped gracefully.

    Using a null object (not None) keeps MemoryBridge's control flow clean —
    it calls the same methods regardless and the results are benign.
    """

    def add(self, entry):
        return entry

    def search(self, query: str, top_k: int = 5, category=None):  # noqa: D401
        return []

    def get_all(self):
        return []

    def delete(self, memory_id: str) -> bool:
        return False

    def apply_decay(self) -> None:
        pass

    def stats(self) -> dict:
        return {"provider": "null", "count": 0, "blocked_by_jurisdiction": True}


def _adapters_from_resolver(domain: str = "core"):
    """Use the unified capability resolver to select knowledge_store adapters.

    Returns (local_adapter, cloud_adapter). The resolver selects adapters in
    cost order and respects jurisdiction / cloud_shift gating, so any configured
    knowledge_store backend in providers.toml is automatically selected without
    code changes.

    When the resolver is unavailable (very early boot / isolated tests), both
    adapters fall back to direct instantiation — same behavior as before the
    capability layer was introduced.

    When the resolver succeeds but finds no permitted cloud adapter (e.g. because
    cloud_shift=False or exec_locus_pin=local blocks it), the cloud slot is
    filled with ``_NullCloudAdapter`` rather than unconditionally constructing
    ``CloudMemoryAdapter``. This ensures jurisdiction gating applies uniformly:
    a blocked cloud backend is NOT silently reintroduced as a fallback.
    """
    local_adapter = None
    cloud_adapter = None
    resolver_succeeded = False
    try:
        from src.capabilities.resolver import get_resolver
        from src.capabilities.adapters.knowledge_store import (
            CogneeKnowledgeAdapter, Mem0KnowledgeAdapter,
        )
        resolver = get_resolver()
        all_adapters = resolver.resolve_all("knowledge_store")
        resolver_succeeded = True
        for cap_adapter in all_adapters:
            if isinstance(cap_adapter, CogneeKnowledgeAdapter) and local_adapter is None:
                local_adapter = LocalMemoryAdapter(domain=domain)
            elif isinstance(cap_adapter, Mem0KnowledgeAdapter) and cloud_adapter is None:
                cloud_adapter = CloudMemoryAdapter(domain=domain)
    except Exception as exc:
        logger.debug("knowledge_store resolver fallback: %s", exc)

    # Local storage is always available — always fall back.
    if local_adapter is None:
        local_adapter = LocalMemoryAdapter(domain=domain)

    if cloud_adapter is None:
        if not resolver_succeeded:
            # Resolver failed (early boot / test isolation): preserve original
            # behavior by instantiating CloudMemoryAdapter as before.
            cloud_adapter = CloudMemoryAdapter(domain=domain)
        else:
            # Resolver ran but selected no permitted cloud adapter (jurisdiction
            # gate excluded it). Use the null adapter so MemoryBridge operates
            # in local-only mode without reintroducing a blocked cloud backend.
            cloud_adapter = _NullCloudAdapter()

    return local_adapter, cloud_adapter

logger = logging.getLogger(__name__)

_SUBSTRATES: Dict[str, "MemorySubstrate"] = {}
_SUBSTRATE_LOCK = threading.Lock()

_AUDIT_MAXLEN = 200


class MemorySubstrate:
    """
    Unified memory substrate.  Callers use this; they never touch Cognee
    or Mem0 directly.
    """

    def __init__(self, domain: str = "core") -> None:
        # Each domain container gets its own substrate bound to its physical DB
        # and isolated vector/KG namespace. ``domain`` decides which database the
        # memory + audit rows land in and which pgvector table is searched.
        self._domain = domain or "core"
        # Adapter selection goes through the unified capability resolver so
        # jurisdiction gating (cloud_shift, exec_locus_pin) and cost-ordered
        # backend selection apply to knowledge_store the same way they apply
        # to LLM and browser. Direct instantiation is the fallback when the
        # resolver is not yet initialized (very early boot or isolated tests).
        self._local, self._cloud = _adapters_from_resolver(self._domain)
        self._bridge = MemoryBridge(self._local, self._cloud)
        self._audit: Deque[MemoryEvent] = deque(maxlen=_AUDIT_MAXLEN)
        self._last_sync: Optional[float] = None
        self._lock = threading.Lock()
        # Hydrate the in-process audit ring from the durable DB log so recent
        # history survives restarts and the dashboard shows it immediately.
        self._hydrate_audit_from_db()

    def _hydrate_audit_from_db(self) -> None:
        try:
            from . import db_store
            for ev in db_store.load_audit_events(limit=_AUDIT_MAXLEN, domain=self._domain):
                self._audit.append(ev)
        except Exception as e:
            logger.warning("MemorySubstrate audit hydrate from DB failed: %s", e)

    def _record_event(self, event: MemoryEvent) -> None:
        """Append an audit event to the in-process ring and the durable DB log."""
        self._audit.append(event)
        try:
            from . import db_store
            db_store.append_audit_event(event, domain=self._domain)
        except Exception as e:
            logger.warning("MemorySubstrate audit DB persist failed: %s", e)

    def _index_vector(self, entry: MemoryEntry) -> None:
        """Embed the entry content and upsert it into this domain's pgvector
        table so semantic recall can use true ANN search.

        Fail-soft: when no embedding backend is available or pgvector is absent,
        this is a no-op and recall falls back to the TF-IDF/lexical path. We
        never fabricate a vector — the absence of an embedding is honest.
        """
        try:
            from src.providers.embeddings import embed_text, get_embedding_backend
            vec = embed_text(entry.content)
            if not vec:
                return
            try:
                model_name = get_embedding_backend().name
            except Exception:
                model_name = ""
            from . import vector_store
            vector_store.upsert(
                self._domain,
                entry.memory_id,
                entry.content,
                vec,
                model_name=model_name,
            )
        except Exception as e:
            logger.debug("MemorySubstrate vector index skipped: %s", e)

    def _vector_recall(self, query: str, top_k: int) -> List[MemoryEntry]:
        """Search this domain's pgvector table and resolve hits to MemoryEntry.

        Returns an empty list when embeddings/pgvector are unavailable so the
        caller transparently falls back to the adapter search path.
        """
        try:
            from src.providers.embeddings import embed_text
            qvec = embed_text(query)
            if not qvec:
                return []
            from . import vector_store
            hits = vector_store.search(self._domain, qvec, top_k=top_k)
            if not hits:
                return []
            resolved: List[MemoryEntry] = []
            for hit in hits:
                mid = hit.get("memory_id")
                if not mid:
                    continue
                entry = None
                try:
                    cache = getattr(self._local, "_local_cache", None)
                    if cache and mid in cache:
                        entry = cache[mid]
                except Exception:
                    entry = None
                if entry is None:
                    entry = MemoryEntry(
                        memory_id=mid,
                        content=hit.get("content", ""),
                        category=hit.get("category", "concept") or "concept",
                        confidence=float(hit.get("score", 0.8) or 0.8),
                        source=hit.get("source", "") or "",
                    )
                resolved.append(entry)
            return resolved
        except Exception as e:
            logger.debug("MemorySubstrate vector recall skipped: %s", e)
            return []

    def write_claim(
        self,
        content: str,
        category: str = "fact",
        confidence: float = 1.0,
        source: str = "",
        verified: bool = False,
        metadata: Optional[Dict] = None,
        local_only: bool = False,
    ) -> MemoryEntry:
        """Persist a claim/fact into long-term memory.

        Private-Mode: when ``local_only=True`` the entry is written ONLY to the
        local KG and tagged ``metadata['private']=True`` so the cloud adapter is
        never touched and the sync bridge will never egress it to the cloud store.
        Use this for sensitive personal/emotional data (the coach's digital twin)."""
        meta = dict(metadata or {})
        if local_only:
            meta["private"] = True
        entry = MemoryEntry(
            memory_id=str(uuid.uuid4()),
            content=content,
            category=category,
            confidence=confidence,
            source=source,
            verified=verified,
            metadata=meta,
        )
        with self._lock:
            if not local_only:
                # Cloud.add may reassign entry.memory_id to a cloud-assigned id.
                # By calling cloud first, local.add then uses the final stable id,
                # keeping both stores keyed by the same memory_id.
                self._cloud.add(entry)
            self._local.add(entry)
            # Index into this domain's pgvector space for true semantic recall.
            # Runs under the same lock so the vector row is keyed by the final
            # (post-cloud) memory_id. Fail-soft: no-op without embeddings/pgvector.
            self._index_vector(entry)

        scope = "private/local-only" if local_only else "local+cloud"
        event = MemoryEvent(
            event_type=MemoryEventType.PERSIST,
            detail=f"Persisted [{category}] ({scope}, conf={confidence:.2f}, verified={verified}): {content[:100]}",
            memory_ids=[entry.memory_id],
        )
        self._record_event(event)
        logger.info(event.to_log_str())
        return entry

    def forget(self, memory_ids: List[str]) -> int:
        """Delete memory nodes from BOTH stores by id (Private-Mode purge / right
        to be forgotten). Returns the count successfully removed from the local KG."""
        deleted = 0
        attempted: List[str] = []
        with self._lock:
            for mid in memory_ids:
                if not mid:
                    continue
                attempted.append(mid)
                local_ok = False
                try:
                    local_ok = bool(self._local.delete(mid))
                except Exception as e:
                    logger.warning("forget local delete failed for %s: %s", mid, e)
                try:
                    self._cloud.delete(mid)
                except Exception as e:
                    logger.warning("forget cloud delete failed for %s: %s", mid, e)
                if local_ok:
                    deleted += 1
        event = MemoryEvent(
            event_type=MemoryEventType.PERSIST,
            detail=f"Forgot {deleted} memory node(s) (Private-Mode purge)",
            memory_ids=attempted[:5],
        )
        self._record_event(event)
        logger.info(event.to_log_str())
        return deleted

    def recall(
        self,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
        min_confidence: float = 0.3,
    ) -> List[MemoryEntry]:
        """Recall semantically related memories for a given query."""
        results: List[MemoryEntry] = []
        seen_ids: set = set()

        # True ANN recall over this domain's pgvector space first (when an
        # embedding backend + pgvector are available). Honest no-op otherwise.
        try:
            for e in self._vector_recall(query, top_k):
                if category is not None and e.category != category:
                    continue
                if e.memory_id not in seen_ids:
                    results.append(e)
                    seen_ids.add(e.memory_id)
        except Exception as e:
            logger.warning("Recall vector search failed: %s", e)

        try:
            local = self._local.search(query, top_k=top_k, category=category)
            for e in local:
                if e.memory_id not in seen_ids:
                    results.append(e)
                    seen_ids.add(e.memory_id)
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
            self._record_event(event)
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
            self._record_event(ev)
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


def get_substrate(domain: str = "core") -> MemorySubstrate:
    """Return the process-wide shared MemorySubstrate for *domain* (lazy init).

    Each domain container gets its own substrate bound to its physical DB and
    isolated vector/KG namespace, so a finance recall never sees fleet memory.
    Callers that don't care about isolation get the shared ``core`` substrate.
    """
    slug = domain or "core"
    inst = _SUBSTRATES.get(slug)
    if inst is None:
        with _SUBSTRATE_LOCK:
            inst = _SUBSTRATES.get(slug)
            if inst is None:
                inst = MemorySubstrate(domain=slug)
                _SUBSTRATES[slug] = inst
    return inst


# Type alias for compatibility
from typing import Any  # noqa: E402
