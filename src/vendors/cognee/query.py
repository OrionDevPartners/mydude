"""
SemanticQuery — query interface over the local KnowledgeGraph.

Adapted from Cognee's query module (Apache-2.0).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .graph import KnowledgeGraph, Node

logger = logging.getLogger(__name__)

_SHARED_GRAPH: Optional[KnowledgeGraph] = None


def get_shared_graph() -> KnowledgeGraph:
    global _SHARED_GRAPH
    if _SHARED_GRAPH is None:
        _SHARED_GRAPH = KnowledgeGraph()
    return _SHARED_GRAPH


class SemanticQuery:
    """High-level query API over a KnowledgeGraph instance."""

    def __init__(self, graph: Optional[KnowledgeGraph] = None) -> None:
        self._graph = graph or get_shared_graph()

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        try:
            results = self._graph.semantic_search(query, top_k=top_k)
            out = []
            for node, score in results:
                attrs = node.attributes or {}
                out.append({
                    "node_id": node.node_id,
                    # memory_id stored in attributes by LocalMemoryAdapter.add()
                    # (UUID/cloud id); falls back to slug for legacy nodes.
                    "memory_id": attrs.get("memory_id") or node.node_id,
                    "label": node.label,
                    "entity_type": node.entity_type,
                    "confidence": node.confidence,
                    "source": node.source,
                    "score": score,
                    "attributes": attrs,
                })
            return out
        except Exception as e:
            logger.warning("SemanticQuery.search failed: %s", e)
            return []

    def remove_node(self, node_id: str) -> bool:
        """Remove a node (and its edges) from the graph and persist."""
        try:
            return self._graph.remove_node(node_id)
        except Exception as e:
            logger.warning("SemanticQuery.remove_node failed: %s", e)
            return False

    def find_contradictions(self, claim: str, threshold: float = 0.25) -> List[Dict]:
        try:
            return self._graph.contradiction_search(claim, threshold=threshold)
        except Exception as e:
            logger.warning("SemanticQuery.find_contradictions failed: %s", e)
            return []

    def ingest(self, text: str, source: str = "") -> int:
        """Ingest *text* into the graph — extract entities/relations and add them."""
        from .extractor import extract_entities_and_relations
        try:
            result = extract_entities_and_relations(text)
            added = 0
            for entity in result.entities:
                self._graph.add_node(
                    label=entity.text,
                    entity_type=entity.entity_type,
                    source=source,
                )
                added += 1
            for rel in result.relations:
                try:
                    self._graph.add_edge(
                        src_label=rel.subject,
                        dst_label=rel.obj,
                        relation=rel.predicate,
                    )
                except Exception:
                    pass
            return added
        except Exception as e:
            logger.warning("SemanticQuery.ingest failed: %s", e)
            return 0

    def stats(self) -> Dict:
        return self._graph.stats()
