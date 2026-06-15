"""Vector search capability adapters.

Two real adapters for the "vector_search" category:

  * ``EmbeddingVectorAdapter`` — wraps the existing ``src.providers.embeddings``
    module (sentence-transformers or OpenAI-compatible endpoint, cached/TTL'd).
    Available when at least one embedding backend is live.

  * ``TFIDFVectorAdapter`` — pure-Python lexical TF-IDF fallback. Always
    available; no dependencies, no secrets.

Both are fully operative real implementations — no placeholders
(Governance Pillar #1).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.capabilities.base import CapabilityAdapter, CapabilitySpec

logger = logging.getLogger(__name__)


class EmbeddingVectorAdapter(CapabilityAdapter):
    """Semantic vector search via the existing embedding backend resolver.

    Delegates to ``src.providers.embeddings.get_embedding_backend()`` which
    already handles: local sentence-transformers, local Ollama/MLX, cloud
    OpenAI-compatible endpoints, cloud_shift gating, and a 30-second TTL cache.
    This adapter simply surfaces that resolution as a governed capability.
    """

    def _probe(self) -> bool:
        try:
            from src.providers.embeddings import embeddings_available
            return embeddings_available()
        except Exception:
            return False

    def health_probe(self) -> Dict[str, Any]:
        try:
            from src.providers.embeddings import get_embedding_backend
            backend = get_embedding_backend()
            if backend is None:
                return {
                    "ok": False,
                    "detail": "no embedding backend available (set EMBEDDING_MODEL or "
                              "register a model in the local model registry)",
                    "exec_locus": self.exec_locus,
                }
            return {
                "ok": True,
                "detail": "available — backend: %s (exec_locus=%s)"
                          % (backend.name, backend.exec_locus),
                "exec_locus": backend.exec_locus,
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc), "exec_locus": self.exec_locus}

    def embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed texts using the active backend, or None on failure."""
        try:
            from src.providers.embeddings import embed_texts
            return embed_texts(texts)
        except Exception:
            return None

    def similarity(self, text_a: str, text_b: str) -> Optional[float]:
        """Cosine similarity of two texts, or None when unavailable."""
        try:
            from src.providers.embeddings import similarity
            return similarity(text_a, text_b)
        except Exception:
            return None


class TFIDFVectorAdapter(CapabilityAdapter):
    """Lexical TF-IDF vector search — always available, no deps, no secrets.

    This is the deterministic local fallback when no embedding model is
    configured. Used by the memory substrate and consistency checker for
    lexical-overlap based recall and contradiction detection.
    """

    def _probe(self) -> bool:
        return True

    def health_probe(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "detail": "available (pure-Python TF-IDF, always local)",
            "exec_locus": "local",
        }

    @property
    def exec_locus(self) -> str:
        return "local"

    def similarity(self, text_a: str, text_b: str) -> float:
        """TF-IDF cosine similarity of two texts."""
        return _tfidf_similarity(text_a, text_b)

    def rank(self, query: str, candidates: List[str]) -> List[float]:
        """TF-IDF cosine similarity of ``query`` against each candidate."""
        if not candidates:
            return []
        return [_tfidf_similarity(query, c) for c in candidates]


# ---------------------------------------------------------------------------
# Shared TF-IDF implementation (replicates what the memory substrate uses)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


def _tf(tokens: List[str]) -> Dict[str, float]:
    from collections import Counter
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


def _tfidf_similarity(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    import math
    tfa, tfb = _tf(ta), _tf(tb)
    vocab = set(tfa) | set(tfb)
    # Simplified IDF: log(2 / (1 + df)) for 2-document corpus
    idf = {term: math.log(2.0 / (1.0 + (1 if term in tfa and term in tfb else 0) + 1))
           for term in vocab}
    va = {t: tfa.get(t, 0.0) * idf[t] for t in vocab}
    vb = {t: tfb.get(t, 0.0) * idf[t] for t in vocab}
    dot = sum(va[t] * vb[t] for t in vocab)
    na = math.sqrt(sum(x * x for x in va.values()))
    nb = math.sqrt(sum(x * x for x in vb.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
