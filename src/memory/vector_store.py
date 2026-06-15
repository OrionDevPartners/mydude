"""Per-domain pgvector semantic-search store (raw SQL, no python pgvector dep).

Each domain database carries its own ``vector_entries`` table (created by
``src.database._ensure_vector_table``) holding the dense embeddings of that
domain's long-term memory. Search is physically isolated: a query against domain
``finance`` can only ever see ``finance`` vectors because it runs on the finance
engine against the finance database.

Why raw SQL? The Postgres ``vector`` extension provides the column type and the
``<=>`` (cosine distance) operator server-side; the Python ``pgvector`` package is
only a convenience adapter for SQLAlchemy column types. Avoiding it keeps the
dependency closure unchanged (the project's uv lock is pinned/frozen) while still
using *real* pgvector — no placeholder, no in-Python brute force.

Governance:
  * **No placeholder / fail-loud-soft** — when the extension is genuinely absent
    the store degrades to a no-op (empty search results) and logs once, so the
    caller's TF-IDF path stays authoritative. It never fabricates similarity.
  * **Provider-agnostic** — embeddings come from ``src.providers.embeddings``
    (whatever backend is resolved); this layer only persists/searches vectors.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Sequence

from sqlalchemy import text

from src.database import get_engine
from src.domains.registry import VECTOR_TABLE, resolve_domain

logger = logging.getLogger(__name__)

# Per-domain availability cache: None = unknown, True/False = last probe result.
_available: Dict[str, Optional[bool]] = {}
_avail_lock = threading.Lock()


def _format_vector(embedding: Sequence[float]) -> str:
    """Render a float vector as pgvector's text input form ``[a,b,c]``."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _probe_available(domain: str) -> bool:
    """Check (and cache) whether this domain's DB has pgvector + the table."""
    slug = resolve_domain(domain)
    cached = _available.get(slug)
    if cached is not None:
        return cached
    with _avail_lock:
        cached = _available.get(slug)
        if cached is not None:
            return cached
        ok = False
        try:
            engine = get_engine(slug)
            with engine.connect() as conn:
                has_ext = conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                ).scalar()
                if has_ext:
                    conn.execute(text("SELECT 1 FROM %s LIMIT 1" % VECTOR_TABLE))
                    ok = True
        except Exception as exc:
            logger.info("vector_store: unavailable for domain=%s (%s)", slug, exc)
            ok = False
        _available[slug] = ok
        return ok


def reset_availability_cache() -> None:
    """Drop the cached probe results (tests / after init_db)."""
    with _avail_lock:
        _available.clear()


def is_available(domain: Optional[str] = None) -> bool:
    return _probe_available(resolve_domain(domain))


def upsert(
    domain: Optional[str],
    memory_id: str,
    content: str,
    embedding: Sequence[float],
    model_name: str = "",
) -> bool:
    """Insert/update one embedding row in *domain*'s vector table.

    Returns False (without raising) when the vector store is unavailable or the
    embedding is empty, so callers can proceed on TF-IDF.
    """
    if not memory_id or not embedding:
        return False
    slug = resolve_domain(domain)
    if not _probe_available(slug):
        return False
    dim = len(embedding)
    vec = _format_vector(embedding)
    stmt = text(
        "INSERT INTO %s (domain, memory_id, content, model_name, dim, embedding, created_at) "
        "VALUES (:domain, :mid, :content, :model, :dim, CAST(:emb AS vector), :ts) "
        "ON CONFLICT (domain, memory_id) DO UPDATE SET "
        "content = EXCLUDED.content, model_name = EXCLUDED.model_name, "
        "dim = EXCLUDED.dim, embedding = EXCLUDED.embedding" % VECTOR_TABLE
    )
    try:
        engine = get_engine(slug)
        with engine.begin() as conn:
            conn.execute(stmt, {
                "domain": slug,
                "mid": memory_id,
                "content": content or "",
                "model": model_name or "",
                "dim": dim,
                "emb": vec,
                "ts": time.time(),
            })
        return True
    except Exception as exc:
        logger.warning("vector_store.upsert(domain=%s, %s) failed: %s", slug, memory_id, exc)
        return False


def search(
    domain: Optional[str],
    embedding: Sequence[float],
    top_k: int = 5,
    min_score: float = 0.0,
) -> List[Dict[str, object]]:
    """Cosine-similarity search within *domain*'s vector space.

    Returns a list of ``{"memory_id", "content", "score"}`` ordered by descending
    similarity (score = 1 - cosine_distance). Only vectors of the SAME dimension
    as the query are compared. Empty list when unavailable — never raises.
    """
    if not embedding:
        return []
    slug = resolve_domain(domain)
    if not _probe_available(slug):
        return []
    dim = len(embedding)
    vec = _format_vector(embedding)
    try:
        top_k = max(1, int(top_k))
    except Exception:
        top_k = 5
    stmt = text(
        "SELECT memory_id, content, 1 - (embedding <=> CAST(:emb AS vector)) AS score "
        "FROM %s WHERE dim = :dim "
        "ORDER BY embedding <=> CAST(:emb AS vector) ASC LIMIT :k" % VECTOR_TABLE
    )
    try:
        engine = get_engine(slug)
        with engine.connect() as conn:
            rows = conn.execute(stmt, {"emb": vec, "dim": dim, "k": top_k}).fetchall()
        out: List[Dict[str, object]] = []
        for r in rows:
            score = float(r.score) if r.score is not None else 0.0
            if score < min_score:
                continue
            out.append({"memory_id": r.memory_id, "content": r.content, "score": score})
        return out
    except Exception as exc:
        logger.warning("vector_store.search(domain=%s) failed: %s", slug, exc)
        return []


def delete(domain: Optional[str], memory_id: str) -> bool:
    """Remove one embedding row from *domain*'s vector table."""
    if not memory_id:
        return False
    slug = resolve_domain(domain)
    if not _probe_available(slug):
        return False
    stmt = text("DELETE FROM %s WHERE domain = :domain AND memory_id = :mid" % VECTOR_TABLE)
    try:
        engine = get_engine(slug)
        with engine.begin() as conn:
            conn.execute(stmt, {"domain": slug, "mid": memory_id})
        return True
    except Exception as exc:
        logger.warning("vector_store.delete(domain=%s, %s) failed: %s", slug, memory_id, exc)
        return False


def count(domain: Optional[str] = None) -> int:
    """Number of vectors stored for *domain* (0 when unavailable)."""
    slug = resolve_domain(domain)
    if not _probe_available(slug):
        return 0
    try:
        engine = get_engine(slug)
        with engine.connect() as conn:
            return int(conn.execute(
                text("SELECT COUNT(*) FROM %s" % VECTOR_TABLE)
            ).scalar() or 0)
    except Exception:
        return 0
