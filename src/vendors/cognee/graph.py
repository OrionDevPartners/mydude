"""
KnowledgeGraph — local embedded knowledge graph.

Nodes are named entities; edges are typed relations.
The graph is persisted as a JSON file under COGNEE_DATA_DIR
(default: .cognee_data/graph.json).

Adapted from Cognee's core graph module (Apache-2.0).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.getenv("COGNEE_DATA_DIR", ".cognee_data"))
_GRAPH_FILE = _DATA_DIR / "graph.json"
# Reentrant: add_edge() may call add_node() while already holding the lock when
# a relation's endpoint nodes don't yet exist. A plain Lock deadlocks there.
_LOCK = threading.RLock()


@dataclass
class Node:
    node_id: str
    label: str
    entity_type: str = "concept"
    confidence: float = 1.0
    source: str = ""
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    access_count: int = 0
    decay: float = 1.0
    attributes: Dict = field(default_factory=dict)


@dataclass
class Edge:
    edge_id: str
    src: str
    dst: str
    relation: str
    weight: float = 1.0
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)


def _tfidf_vector(text: str, corpus: List[str]) -> Dict[str, float]:
    """Return a sparse TF-IDF-ish vector for *text* against *corpus*."""
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "and", "or", "but", "if",
        "its", "this", "that", "it", "not", "no",
    }
    tokens = [w for w in re.split(r"\W+", text.lower()) if len(w) > 2 and w not in stop]
    tf: Dict[str, float] = defaultdict(float)
    for t in tokens:
        tf[t] += 1.0
    if tokens:
        mx = max(tf.values())
        tf = {k: v / mx for k, v in tf.items()}

    N = len(corpus) + 1
    idf: Dict[str, float] = {}
    all_tokens = set(tf.keys())
    for t in all_tokens:
        df = sum(1 for doc in corpus if t in doc.lower()) + 1
        idf[t] = math.log(N / df)

    return {t: tf[t] * idf[t] for t in tf}


def _cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    if not v1 or not v2:
        return 0.0
    common = set(v1) & set(v2)
    dot = sum(v1[k] * v2[k] for k in common)
    mag1 = math.sqrt(sum(x * x for x in v1.values()))
    mag2 = math.sqrt(sum(x * x for x in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


class KnowledgeGraph:
    """Embedded knowledge graph with TF-IDF semantic similarity and persistence."""

    def __init__(self) -> None:
        self._nodes: Dict[str, Node] = {}
        self._edges: Dict[str, Edge] = {}
        self._adj: Dict[str, List[str]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            if _GRAPH_FILE.exists():
                with open(_GRAPH_FILE, "r") as f:
                    data = json.load(f)
                for nd in data.get("nodes", []):
                    n = Node(**nd)
                    self._nodes[n.node_id] = n
                for ed in data.get("edges", []):
                    e = Edge(**ed)
                    self._edges[e.edge_id] = e
                    self._adj[e.src].append(e.dst)
        except Exception as exc:
            logger.warning("KnowledgeGraph load failed (starting fresh): %s", exc)

    def _save(self) -> None:
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "nodes": [asdict(n) for n in self._nodes.values()],
                "edges": [asdict(e) for e in self._edges.values()],
            }
            tmp = str(_GRAPH_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, _GRAPH_FILE)
        except Exception as exc:
            logger.warning("KnowledgeGraph save failed: %s", exc)

    def add_node(self, label: str, entity_type: str = "concept",
                 confidence: float = 1.0, source: str = "",
                 attributes: Optional[Dict] = None) -> Node:
        with _LOCK:
            node_id = re.sub(r"\W+", "_", label.lower())[:80]
            if node_id in self._nodes:
                n = self._nodes[node_id]
                n.last_seen = time.time()
                n.access_count += 1
                n.confidence = max(n.confidence, confidence)
            else:
                n = Node(
                    node_id=node_id,
                    label=label,
                    entity_type=entity_type,
                    confidence=confidence,
                    source=source,
                    attributes=attributes or {},
                )
                self._nodes[node_id] = n
            self._save()
            return n

    def add_edge(self, src_label: str, dst_label: str, relation: str,
                 weight: float = 1.0, confidence: float = 1.0) -> Edge:
        with _LOCK:
            src_id = re.sub(r"\W+", "_", src_label.lower())[:80]
            dst_id = re.sub(r"\W+", "_", dst_label.lower())[:80]
            if src_id not in self._nodes:
                self.add_node(src_label)
            if dst_id not in self._nodes:
                self.add_node(dst_label)
            edge_id = f"{src_id}__{relation}__{dst_id}"
            if edge_id not in self._edges:
                e = Edge(
                    edge_id=edge_id,
                    src=src_id,
                    dst=dst_id,
                    relation=relation,
                    weight=weight,
                    confidence=confidence,
                )
                self._edges[edge_id] = e
                self._adj[src_id].append(dst_id)
            self._save()
            return self._edges[edge_id]

    def semantic_search(self, query: str, top_k: int = 5,
                        min_score: float = 0.05) -> List[Tuple[Node, float]]:
        """Return the top_k nodes most semantically similar to *query*.

        Uses real vector embeddings when an embedding backend is available
        (genuinely semantic recall — catches paraphrases that share no terms),
        and transparently falls back to lexical TF-IDF cosine otherwise.
        """
        nodes = list(self._nodes.values())
        if not nodes:
            return []
        texts = [n.label + " " + n.entity_type + " " + n.source for n in nodes]

        emb_scores = self._embedding_scores(query, texts)
        scored: List[Tuple[Node, float]] = []
        if emb_scores is not None:
            for node, sim in zip(nodes, emb_scores):
                score = sim * node.decay * node.confidence
                if score >= min_score:
                    scored.append((node, round(score, 4)))
        else:
            q_vec = _tfidf_vector(query, texts)
            for node, text in zip(nodes, texts):
                n_vec = _tfidf_vector(text, texts)
                score = _cosine(q_vec, n_vec) * node.decay * node.confidence
                if score >= min_score:
                    scored.append((node, round(score, 4)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _embedding_scores(query: str, texts: List[str]) -> Optional[List[float]]:
        """Cosine similarity of *query* against *texts* using vector embeddings.

        Returns None (never raises) when no embedding backend is available, so
        :meth:`semantic_search` falls back to TF-IDF.
        """
        try:
            from src.providers.embeddings import rank_scores

            return rank_scores(query, texts)
        except Exception:
            return None

    def contradiction_search(self, claim: str,
                             negation_words: Optional[Set[str]] = None,
                             threshold: float = 0.3) -> List[Dict]:
        """
        Return nodes whose content appears to contradict *claim* using
        TF-IDF similarity + negation-pattern detection.

        This replaces the Jaccard keyword check: we find semantically similar
        nodes (shared topic) that contain negation markers near overlapping
        terms — catching cases like 'finish by Friday' vs 'done by Monday'.
        """
        if negation_words is None:
            negation_words = {
                "not", "never", "no", "cannot", "can't", "won't", "doesn't",
                "don't", "didn't", "isn't", "aren't", "wasn't", "weren't",
                "wouldn't", "shouldn't", "couldn't", "without", "lack",
                "absent", "false", "incorrect", "wrong", "invalid",
                "impossible", "unlike", "contrary", "opposite",
            }
        temporal_words = {
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "today", "tomorrow", "yesterday",
            "morning", "afternoon", "evening", "night", "week", "month",
            "year", "deadline", "due", "finish", "complete", "done",
        }
        contradictions = []
        similar = self.semantic_search(claim, top_k=10, min_score=threshold)
        claim_lower = claim.lower()
        claim_words = set(re.split(r"\W+", claim_lower))
        claim_temporal = claim_words & temporal_words

        for node, score in similar:
            node_lower = node.label.lower()
            node_words = set(re.split(r"\W+", node_lower))

            has_negation = bool(node_words & negation_words)
            claim_negated = bool(claim_words & negation_words)
            temporal_conflict = False
            if claim_temporal:
                node_temporal = node_words & temporal_words
                if node_temporal and node_temporal != claim_temporal:
                    temporal_conflict = True

            if has_negation != claim_negated or temporal_conflict:
                contradictions.append({
                    "node_id": node.node_id,
                    "label": node.label,
                    "confidence": node.confidence,
                    "similarity": score,
                    "reason": "temporal_conflict" if temporal_conflict else "negation_mismatch",
                })

        return contradictions

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and all its incident edges, then persist.

        Called by LocalMemoryAdapter.delete() to converge the persisted KG
        with the in-memory cache — prevents stale nodes from surfacing in
        future semantic_search / contradiction_search results.
        """
        with _LOCK:
            if node_id not in self._nodes:
                return False
            del self._nodes[node_id]
            # Remove all edges where this node is src or dst
            to_remove = [eid for eid, e in self._edges.items()
                         if e.src == node_id or e.dst == node_id]
            for eid in to_remove:
                del self._edges[eid]
            # Clean adjacency list
            self._adj.pop(node_id, None)
            for adj_list in self._adj.values():
                try:
                    while node_id in adj_list:
                        adj_list.remove(node_id)
                except ValueError:
                    pass
            self._save()
            return True

    def apply_decay(self, decay_rate: float = 0.01) -> None:
        """Reduce the weight of rarely-accessed nodes over time."""
        with _LOCK:
            now = time.time()
            for node in self._nodes.values():
                age_days = (now - node.last_seen) / 86400.0
                node.decay = max(0.1, node.decay * math.exp(-decay_rate * age_days))
            self._save()

    def stats(self) -> Dict:
        return {
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "data_file": str(_GRAPH_FILE),
        }
