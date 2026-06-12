"""
LocalMemoryAdapter — wraps vendored Cognee KnowledgeGraph for the local side.

The swarm never imports Cognee directly; it always goes through this adapter.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

from .adapter import MemoryAdapterBase, MemoryEntry

logger = logging.getLogger(__name__)


class LocalMemoryAdapter(MemoryAdapterBase):
    """Cognee-backed local KG memory store."""

    def __init__(self) -> None:
        try:
            from src.vendors.cognee.query import SemanticQuery
            from src.vendors.cognee.graph import KnowledgeGraph
            self._graph = KnowledgeGraph()
            self._query = SemanticQuery(self._graph)
            self._available = True
        except Exception as e:
            logger.warning("LocalMemoryAdapter (Cognee) init failed: %s", e)
            self._graph = None
            self._query = None
            self._available = False

        # Populate cache from persisted KG so get_all()/delete() reflect
        # durable local state across process restarts — not just in-memory.
        self._local_cache: Dict[str, MemoryEntry] = {}
        self._restore_cache_from_graph()
        # The DB is the durable source of truth; overlay it last so its
        # fully-attributed entries win over reconstructed KG nodes.
        self._restore_cache_from_db()

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        if not entry.memory_id:
            entry.memory_id = str(uuid.uuid4())
        self._local_cache[entry.memory_id] = entry

        if self._available and self._query:
            try:
                self._query.ingest(
                    entry.content,
                    source=f"memory:{entry.category}:{entry.source}",
                )
                try:
                    self._graph.add_node(
                        label=entry.content[:120],
                        entity_type=entry.category,
                        confidence=entry.confidence * entry.decay,
                        source=entry.source,
                        attributes={
                            "memory_id": entry.memory_id,
                            "verified": entry.verified,
                        },
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.warning("LocalMemoryAdapter.add KG ingest failed: %s", e)

        # Flush to the durable DB store so the entry survives process restarts.
        try:
            from . import db_store
            db_store.upsert_entry("local", entry)
        except Exception as e:
            logger.warning("LocalMemoryAdapter.add DB persist failed: %s", e)

        return entry

    def search(self, query: str, top_k: int = 5,
               category: Optional[str] = None) -> List[MemoryEntry]:
        results: List[MemoryEntry] = []

        if self._available and self._query:
            try:
                kg_results = self._query.search(query, top_k=top_k * 2)
                for hit in kg_results:
                    # SemanticQuery.search() now returns `memory_id` from
                    # node.attributes["memory_id"] (UUID, set by add()), falling
                    # back to the node_id slug for legacy/raw KG nodes.
                    mid = hit.get("memory_id") or hit.get("node_id", "")
                    if mid in self._local_cache:
                        # Canonical path: resolve UUID → cached entry (preserves
                        # `verified` and all other metadata set by add()).
                        e = self._local_cache[mid]
                    else:
                        # Synthetic fallback: reconstruct from KG hit.
                        # Preserve `verified` from node attributes so downstream
                        # KG-support checks (any(e.verified ...)) stay accurate.
                        attrs = hit.get("attributes") or {}
                        e = MemoryEntry(
                            memory_id=mid or str(uuid.uuid4()),
                            content=hit["label"],
                            category=hit.get("entity_type", "concept"),
                            confidence=hit.get("confidence", 0.8),
                            source=hit.get("source", ""),
                            verified=bool(attrs.get("verified", False)),
                        )
                    if category is None or e.category == category:
                        results.append(e)
                    if len(results) >= top_k:
                        break
            except Exception as e:
                logger.warning("LocalMemoryAdapter.search KG failed: %s", e)

        if len(results) < top_k:
            cached = list(self._local_cache.values())
            if category:
                cached = [e for e in cached if e.category == category]
            scored = self._rank_cached(query, cached)
            scored.sort(key=lambda x: x[1], reverse=True)
            seen_ids = {e.memory_id for e in results}
            for e, _ in scored:
                if e.memory_id not in seen_ids:
                    results.append(e)
                    if len(results) >= top_k:
                        break

        for e in results:
            e.access_count += 1
        return results[:top_k]

    def _rank_cached(self, query: str, cached: List[MemoryEntry]):
        """Score cached entries against *query*, weighted by confidence·decay.

        Prefers real vector-embedding cosine (genuinely semantic — recalls
        paraphrases that share no words); falls back to lexical word-overlap
        when no embedding backend is available.
        """
        if not cached:
            return []
        try:
            from src.providers.embeddings import rank_scores

            sims = rank_scores(query, [e.content for e in cached])
        except Exception:
            sims = None

        scored = []
        if sims is not None:
            for e, sim in zip(cached, sims):
                if sim > 0:
                    scored.append((e, sim * e.confidence * e.decay))
        else:
            q_words = set(query.lower().split())
            for e in cached:
                e_words = set(e.content.lower().split())
                overlap = len(q_words & e_words)
                if overlap > 0:
                    scored.append((e, overlap * e.confidence * e.decay))
        return scored

    def find_contradictions(self, claim: str,
                            threshold: float = 0.25) -> List[Dict]:
        if self._available and self._query:
            try:
                return self._query.find_contradictions(claim, threshold=threshold)
            except Exception as e:
                logger.warning("LocalMemoryAdapter.find_contradictions failed: %s", e)
        return []

    def get_all(self) -> List[MemoryEntry]:
        return list(self._local_cache.values())

    def delete(self, memory_id: str) -> bool:
        if memory_id not in self._local_cache:
            return False
        entry = self._local_cache.pop(memory_id)
        # Also remove the corresponding KG node so repeated bridge syncs
        # converge (no stale nodes surface via semantic_search).
        if self._available and self._graph:
            try:
                import re as _re
                node_id_slug = _re.sub(r"\W+", "_", entry.content[:120].lower())[:80]
                removed = self._graph.remove_node(node_id_slug)
                if not removed:
                    logger.debug(
                        "LocalMemoryAdapter.delete: KG node %r not found for %s",
                        node_id_slug, memory_id,
                    )
            except Exception as exc:
                logger.warning("LocalMemoryAdapter.delete KG remove failed: %s", exc)
        # Remove the durable DB row so deletes converge across restarts.
        try:
            from . import db_store
            db_store.delete_entry("local", memory_id)
        except Exception as exc:
            logger.warning("LocalMemoryAdapter.delete DB remove failed: %s", exc)
        return True

    def _restore_cache_from_graph(self) -> None:
        """Populate _local_cache from the persisted KG nodes on startup.

        This ensures get_all()/delete() reflect durable local state even after
        a process restart, aligning the in-memory cache with the JSON-persisted graph.
        """
        if not (self._available and self._graph):
            return
        try:
            for node in self._graph._nodes.values():
                # Prefer the memory_id stored in attributes (written by add()); fall
                # back to node_id so we can still reconstruct entries for KG nodes
                # that were written before this field existed.
                attrs = node.attributes or {}
                mid = attrs.get("memory_id") or node.node_id
                if mid not in self._local_cache:
                    self._local_cache[mid] = MemoryEntry(
                        memory_id=mid,
                        content=node.label,
                        category=node.entity_type,
                        confidence=node.confidence,
                        source=node.source,
                        created_at=node.created_at,
                        updated_at=node.last_seen,
                        access_count=node.access_count,
                        decay=node.decay,
                        verified=bool(attrs.get("verified", False)),
                    )
        except Exception as e:
            logger.warning("LocalMemoryAdapter cache restore from graph failed: %s", e)

    def _restore_cache_from_db(self) -> None:
        """Overlay the durable DB store onto the cache on startup.

        The DB is the durable source of truth for which entries exist, so its
        rows (which carry full metadata written by add()) take precedence over
        entries reconstructed from raw KG nodes.
        """
        try:
            from . import db_store
            for entry in db_store.load_entries("local"):
                self._local_cache[entry.memory_id] = entry
        except Exception as e:
            logger.warning("LocalMemoryAdapter cache restore from DB failed: %s", e)

    def apply_decay(self) -> None:
        if self._available and self._graph:
            try:
                self._graph.apply_decay()
            except Exception as e:
                logger.warning("LocalMemoryAdapter.apply_decay failed: %s", e)

    def stats(self) -> Dict:
        base = {
            "adapter": "cognee_local",
            "cache_entries": len(self._local_cache),
            "available": self._available,
        }
        if self._available and self._graph:
            try:
                base.update(self._graph.stats())
            except Exception:
                pass
        return base
