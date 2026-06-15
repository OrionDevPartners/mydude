"""Mechanical zero-token router.

When an incoming intent matches a known deterministic tool/integration above
``ZERO_TOKEN_THRESHOLD`` (default: 0.92, configurable via env var), the intent
is dispatched straight to that tool without calling the LLM swarm (zero token
cost). Below threshold the router returns ``None`` and the caller falls through
to the normal swarm — fail-open by design.

Embedding backend: uses the provider-agnostic seam in
``src.providers.embeddings`` (falls back to TF-IDF when no backend is
available). The router never raises — any error is logged and None is returned.

Audit: every routing decision (hit or miss) is appended to
``.devguard/routing_audit.jsonl`` (ring-buffer capped at MAX_AUDIT_ENTRIES).
Aggregate stats (total hits, hit rate, last threshold) are maintained in memory
and exposed via ``get_stats()``.

Governance: provider-agnostic (embedding seam), fail-open, audit trail for
every decision, threshold is operator-configurable, no ungoverned LLM output.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimum cosine similarity required to skip the swarm. Very high to ensure
# only unambiguous, deterministic tool matches bypass governance.
ZERO_TOKEN_THRESHOLD = float(os.environ.get("ZERO_TOKEN_THRESHOLD", "0.92"))

# Ring buffer limit for the on-disk audit JSONL.
MAX_AUDIT_ENTRIES = int(os.environ.get("ZERO_TOKEN_MAX_AUDIT", "1000"))

# Max chars from the intent string stored in the audit log (never full prompt).
_MAX_INTENT_CHARS = 120


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class ZeroTokenResult:
    """Decision record for one routing evaluation."""
    capability: str
    score: float
    threshold: float
    dispatched: bool
    intent_snippet: str = ""
    tool_output: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RoutingStats:
    total_evaluations: int = 0
    zero_token_hits: int = 0
    zero_token_misses: int = 0
    threshold: float = ZERO_TOKEN_THRESHOLD
    embedding_backend: str = "none"
    last_hit_capability: Optional[str] = None
    last_hit_score: float = 0.0
    last_reset_at: str = ""

    @property
    def hit_rate(self) -> float:
        if self.total_evaluations == 0:
            return 0.0
        return round(self.zero_token_hits / self.total_evaluations, 4)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hit_rate"] = self.hit_rate
        d["hit_rate_pct"] = round(self.hit_rate * 100, 1)
        return d


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #

def _audit_path() -> Path:
    env = os.environ.get("DEVGUARD_ROUTING_AUDIT_PATH")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    data_dir = repo_root / ".devguard"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "routing_audit.jsonl"


def _stats_path() -> Path:
    return _audit_path().parent / "routing_stats.json"


def _load_persisted_stats() -> Optional[Dict]:
    try:
        p = _stats_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _save_persisted_stats(stats: RoutingStats) -> None:
    try:
        _stats_path().write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")
    except Exception:
        pass


def _append_audit(entry: dict) -> None:
    """Append one JSONL entry; truncate if over MAX_AUDIT_ENTRIES."""
    try:
        p = _audit_path()
        lines: List[str] = []
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(entry))
        # Keep only the tail of the ring buffer.
        if len(lines) > MAX_AUDIT_ENTRIES:
            lines = lines[-MAX_AUDIT_ENTRIES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.debug("zero_token_router: audit write failed: %s", exc)


# --------------------------------------------------------------------------- #
# Embedding helpers
# --------------------------------------------------------------------------- #

def _embed(text: str) -> Optional[List[float]]:
    """Embed text via the provider-agnostic backend; None on unavailability."""
    try:
        from src.providers.embeddings import embed_text
        return embed_text(text)
    except Exception:
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    try:
        from src.providers.embeddings import cosine
        return cosine(a, b)
    except Exception:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


def _tfidf_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
    from src.swarm.ast_router import tfidf_score
    return tfidf_score(query_tokens, doc_tokens)


# --------------------------------------------------------------------------- #
# Main router class
# --------------------------------------------------------------------------- #

class ZeroTokenRouter:
    """Route intents to deterministic tools when similarity is above threshold.

    Thread-safe (single lock guards stats mutation). The capability index is
    built lazily on first use and refreshed every ``_INDEX_TTL`` seconds.
    """

    _INDEX_TTL = 120.0  # seconds between index refreshes

    def __init__(self, threshold: float = ZERO_TOKEN_THRESHOLD) -> None:
        self.threshold = threshold
        self._lock = threading.Lock()
        self._stats = RoutingStats(threshold=threshold)
        self._index: Optional[List[Dict]] = None
        self._index_ts: float = 0.0
        self._restore_stats()

    def _restore_stats(self) -> None:
        saved = _load_persisted_stats()
        if saved:
            try:
                self._stats.total_evaluations = int(saved.get("total_evaluations", 0))
                self._stats.zero_token_hits = int(saved.get("zero_token_hits", 0))
                self._stats.zero_token_misses = int(saved.get("zero_token_misses", 0))
                self._stats.last_hit_capability = saved.get("last_hit_capability")
                self._stats.last_hit_score = float(saved.get("last_hit_score", 0))
                self._stats.last_reset_at = saved.get("last_reset_at", "")
            except Exception:
                pass

    def _build_index(self) -> List[Dict]:
        """Build the capability description index (text + optional embeddings)."""
        try:
            from src.swarm.ast_router import build_capability_signatures
            sigs = build_capability_signatures()
        except Exception as exc:
            logger.warning("zero_token_router: signature build failed: %s", exc)
            sigs = {}

        try:
            from src.providers.embeddings import get_embedding_backend
            backend = get_embedding_backend()
            self._stats.embedding_backend = backend.name if backend else "tfidf-fallback"
        except Exception:
            self._stats.embedding_backend = "tfidf-fallback"

        index: List[Dict] = []
        for cap_name, sig in sigs.items():
            text = sig.structural_text()
            vec = _embed(text)
            index.append({
                "capability": cap_name,
                "text": text,
                "vec": vec,
                "tokens": re.findall(r"[a-z][a-z0-9_]*", text.lower()),
            })
        logger.info("zero_token_router: indexed %d capabilities", len(index))
        return index

    def _get_index(self) -> List[Dict]:
        now = time.monotonic()
        with self._lock:
            if self._index is None or (now - self._index_ts) > self._INDEX_TTL:
                self._index = self._build_index()
                self._index_ts = now
        return self._index

    def score_intent(self, intent: str) -> List[Dict]:
        """Score the intent against every indexed capability.

        Returns a list of ``{capability, score}`` dicts sorted desc by score.
        """
        index = self._get_index()
        intent_vec = _embed(intent)
        intent_tokens = re.findall(r"[a-z][a-z0-9_]*", intent.lower())
        results = []
        for entry in index:
            if intent_vec and entry.get("vec"):
                score = _cosine(intent_vec, entry["vec"])
            else:
                score = _tfidf_score(intent_tokens, entry["tokens"])
            results.append({"capability": entry["capability"], "score": round(score, 4)})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    async def async_route(
        self,
        intent: str,
        *,
        session_id: Optional[str] = None,
        broker: Optional[Any] = None,
    ) -> Optional["ZeroTokenResult"]:
        """Async routing with real broker dispatch via ``await broker.request()``.

        Call this from async contexts (e.g. WaveOrchestrator.run) instead of
        the synchronous ``route()`` so the capability is truly dispatched and
        the wave loop can be short-circuited on a hit.

        Returns a ZeroTokenResult with ``dispatched=True`` when the capability
        was invoked via the broker above threshold.  Returns ``None`` when below
        threshold (swarm should proceed normally).
        """
        t0 = time.monotonic()
        intent_snippet = (intent or "")[:_MAX_INTENT_CHARS]

        try:
            scores = self.score_intent(intent)
            if not scores:
                return None

            top = scores[0]
            cap_name = top["capability"]
            score = top["score"]

            dispatched = False
            tool_output: Optional[str] = None
            error: Optional[str] = None

            with self._lock:
                self._stats.total_evaluations += 1
                if score >= self.threshold:
                    self._stats.zero_token_hits += 1
                    self._stats.last_hit_capability = cap_name
                    self._stats.last_hit_score = score
                else:
                    self._stats.zero_token_misses += 1

            if score >= self.threshold and broker is not None:
                try:
                    br = await broker.request(
                        cap_name,
                        {"source": "zero_token_router", "intent": intent_snippet},
                    )
                    dispatched = br.ok
                    if br.output is not None:
                        tool_output = str(br.output)
                    elif br.decision is not None:
                        tool_output = br.decision.reason
                except Exception as exc:
                    error = f"dispatch_error: {exc}"
                    logger.warning(
                        "zero_token_router: async dispatch of %r failed: %s",
                        cap_name, exc,
                    )

            elapsed = round((time.monotonic() - t0) * 1000, 2)
            result_obj = ZeroTokenResult(
                capability=cap_name,
                score=score,
                threshold=self.threshold,
                dispatched=dispatched,
                intent_snippet=intent_snippet,
                tool_output=tool_output,
                error=error,
                elapsed_ms=elapsed,
            )

            try:
                import threading as _threading
                _threading.Thread(
                    target=lambda: (
                        _append_audit({
                            "ts": time.time(),
                            "capability": cap_name,
                            "score": score,
                            "threshold": self.threshold,
                            "dispatched": dispatched,
                            "session_id": session_id,
                            "elapsed_ms": elapsed,
                        }),
                        _save_persisted_stats(self._stats),
                    ),
                    daemon=True,
                ).start()
            except Exception:
                pass

            return result_obj if score >= self.threshold else None

        except Exception as exc:
            logger.warning("zero_token_router: async_route() failed: %s", exc)
            return None

    def route(
        self,
        intent: str,
        *,
        session_id: Optional[str] = None,
        broker: Optional[Any] = None,
    ) -> Optional[ZeroTokenResult]:
        """Attempt a zero-token route for ``intent``.

        Returns a ``ZeroTokenResult`` with ``dispatched=True`` if a match above
        threshold was found and the broker dispatch succeeded. Returns ``None``
        (or a result with ``dispatched=False``) when the swarm should handle it.

        ``broker`` is the live ``CapabilityBroker`` instance; when provided and
        a high-confidence match is found, the capability is dispatched directly.
        When ``broker`` is None, the result records the match but cannot dispatch.
        """
        t0 = time.monotonic()
        intent_snippet = (intent or "")[:_MAX_INTENT_CHARS]

        try:
            scores = self.score_intent(intent)
            if not scores:
                return None

            top = scores[0]
            cap_name = top["capability"]
            score = top["score"]

            dispatched = False
            tool_output = None
            error = None

            if score >= self.threshold:
                if broker is not None:
                    try:
                        import asyncio
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Can't await from sync context; record as match-only
                            logger.info(
                                "zero_token_router: high-confidence match %r (%.3f) "
                                "but cannot dispatch synchronously; swarm will handle.",
                                cap_name, score,
                            )
                        else:
                            result = loop.run_until_complete(
                                broker.request(cap_name, {"source": "zero_token_router"})
                            )
                            dispatched = True
                            tool_output = result.output or result.decision.reason
                    except Exception as exc:
                        error = f"dispatch_error: {exc}"
                        logger.warning(
                            "zero_token_router: dispatch of %r failed: %s", cap_name, exc
                        )
                else:
                    # No broker available — record match for audit, swarm handles
                    dispatched = False

            elapsed = round((time.monotonic() - t0) * 1000, 2)
            result_obj = ZeroTokenResult(
                capability=cap_name,
                score=score,
                threshold=self.threshold,
                dispatched=dispatched,
                intent_snippet=intent_snippet,
                tool_output=tool_output,
                error=error,
                elapsed_ms=elapsed,
            )

            # Update stats
            with self._lock:
                self._stats.total_evaluations += 1
                if score >= self.threshold:
                    self._stats.zero_token_hits += 1
                    self._stats.last_hit_capability = cap_name
                    self._stats.last_hit_score = score
                else:
                    self._stats.zero_token_misses += 1

            # Async audit write (best-effort, off the hot path)
            try:
                import threading as _threading
                _threading.Thread(
                    target=_append_audit,
                    args=({
                        "ts": time.time(),
                        "capability": cap_name,
                        "score": score,
                        "threshold": self.threshold,
                        "dispatched": dispatched,
                        "session_id": session_id,
                        "elapsed_ms": elapsed,
                    },),
                    daemon=True,
                ).start()
                _save_persisted_stats(self._stats)
            except Exception:
                pass

            return result_obj if score >= self.threshold else None

        except Exception as exc:
            logger.warning("zero_token_router: route() failed: %s", exc)
            return None

    def get_stats(self) -> RoutingStats:
        with self._lock:
            return RoutingStats(
                total_evaluations=self._stats.total_evaluations,
                zero_token_hits=self._stats.zero_token_hits,
                zero_token_misses=self._stats.zero_token_misses,
                threshold=self._stats.threshold,
                embedding_backend=self._stats.embedding_backend,
                last_hit_capability=self._stats.last_hit_capability,
                last_hit_score=self._stats.last_hit_score,
                last_reset_at=self._stats.last_reset_at,
            )

    def reset_stats(self) -> None:
        from datetime import datetime, timezone
        with self._lock:
            self._stats.total_evaluations = 0
            self._stats.zero_token_hits = 0
            self._stats.zero_token_misses = 0
            self._stats.last_hit_capability = None
            self._stats.last_hit_score = 0.0
            self._stats.last_reset_at = datetime.now(timezone.utc).isoformat()
        _save_persisted_stats(self._stats)

    def invalidate_index(self) -> None:
        """Force a rebuild of the capability index on next call."""
        with self._lock:
            self._index = None
            self._index_ts = 0.0


# --------------------------------------------------------------------------- #
# Module-level singleton (one router per process)
# --------------------------------------------------------------------------- #

_ROUTER_LOCK = threading.Lock()
_ROUTER: Optional[ZeroTokenRouter] = None


def get_router() -> ZeroTokenRouter:
    """Return the process-level ZeroTokenRouter singleton."""
    global _ROUTER
    if _ROUTER is None:
        with _ROUTER_LOCK:
            if _ROUTER is None:
                _ROUTER = ZeroTokenRouter()
    return _ROUTER


def get_routing_stats() -> RoutingStats:
    """Convenience: get stats without holding a router reference."""
    return get_router().get_stats()
