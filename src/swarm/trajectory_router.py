"""Conversational trajectory / momentum router.

Maintains a sliding window (last ``TRAJECTORY_WINDOW`` turns, default 5) of
turn text per logical session. The weighted average of recent turn embeddings
forms a momentum vector that biases subsequent benchmark routing category
decisions toward topics the conversation has been drifting toward.

Pre-emptive hazard signals are raised when the trajectory momentum vector's
cosine similarity with hazard anchors (security, ethics, compliance) exceeds
``HAZARD_THRESHOLD`` — these appear in the routing record so the orchestrator
can act before the turn hits the swarm.

Embedding backend: uses the provider-agnostic seam in
``src.providers.embeddings``. Falls back to TF-IDF keyword overlap when no
embedding backend is available — gracefully degraded, never raises.

Governance: in-memory only (no DB writes), sessions capped at MAX_SESSIONS,
no raw prompt text is persisted beyond the sliding window (fail-soft, memoryless
after session eviction).
"""
from __future__ import annotations

import collections
import logging
import math
import re
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TRAJECTORY_WINDOW: int = 5
MAX_SESSIONS: int = 200
HAZARD_THRESHOLD: float = 0.72
MOMENTUM_DECAY: float = 0.80

# Hazard anchor phrases — checked against the momentum vector.
# Categories map to benchmark_routing.CATEGORIES for bias integration.
_HAZARD_ANCHORS: Dict[str, str] = {
    "security": (
        "security vulnerability exploit attack harden threat model credential "
        "injection bypass privilege escalation sensitive data breach"
    ),
    "safety": (
        "harm dangerous illegal toxic violent prohibited content policy "
        "violation abuse unsafe behavior"
    ),
    "compliance": (
        "compliance audit regulation GDPR HIPAA legal requirement policy "
        "governance accountability traceability"
    ),
}

# Category anchors for routing bias (aligned with benchmark_routing.CATEGORIES).
_CATEGORY_ANCHORS: Dict[str, str] = {
    "coding": "code function implement debug refactor algorithm program compile test",
    "agentic": "agent workflow orchestrate automate plan execute multi-step pipeline",
    "reasoning": "reason analyze explain trade-off compare evaluate strategy logic",
    "math": "calculate equation probability matrix algebra formula statistics",
    "long_context": "summarize document codebase transcript report many pages synthesize",
    "creative": "story poem creative brainstorm tagline marketing narrative fiction",
    "multilingual": "translate translation french spanish german chinese localize",
    "security": "security vulnerability exploit threat model owasp xss injection harden",
    "frontend_uiux": "ui ux component layout design css responsive accessibility figma",
    "general": "general information question answer explain describe",
}


# --------------------------------------------------------------------------- #
# Embedding helpers (fail-soft)
# --------------------------------------------------------------------------- #

def _embed(text: str) -> Optional[List[float]]:
    try:
        from src.providers.embeddings import embed_text
        return embed_text(text)
    except Exception:
        return None


def _cosine(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
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


def _tfidf_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z][a-z0-9_]*", a.lower()))
    tb = set(re.findall(r"[a-z][a-z0-9_]*", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / (len(ta) ** 0.5 * len(tb) ** 0.5)


# --------------------------------------------------------------------------- #
# Session data
# --------------------------------------------------------------------------- #

@dataclass
class Turn:
    text: str
    timestamp: float = field(default_factory=time.monotonic)
    embedding: Optional[List[float]] = None

    def __post_init__(self) -> None:
        if self.embedding is None:
            self.embedding = _embed(self.text)


@dataclass
class TrajectoryMomentum:
    """Routing bias and hazard hints derived from a session's momentum vector."""
    category_bias: Dict[str, float] = field(default_factory=dict)
    hazard_hints: List[str] = field(default_factory=list)
    dominant_category: str = "general"
    dominant_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "category_bias": dict(self.category_bias),
            "hazard_hints": list(self.hazard_hints),
            "dominant_category": self.dominant_category,
            "dominant_score": round(self.dominant_score, 4),
        }


# --------------------------------------------------------------------------- #
# Session class
# --------------------------------------------------------------------------- #

class TrajectorySession:
    """Maintains a rolling window of turn embeddings for one logical session."""

    def __init__(self, window: int = TRAJECTORY_WINDOW) -> None:
        self.window = window
        self._turns: collections.deque = collections.deque(maxlen=window)
        self._anchor_vecs: Optional[Dict[str, Optional[List[float]]]] = None
        self._hazard_vecs: Optional[Dict[str, Optional[List[float]]]] = None

    def record_turn(self, text: str) -> None:
        """Add a new turn to the sliding window (oldest turn is dropped)."""
        if not text or not text.strip():
            return
        self._turns.append(Turn(text=text[:2000]))

    def _get_anchor_vecs(self) -> Dict[str, Optional[List[float]]]:
        if self._anchor_vecs is None:
            self._anchor_vecs = {cat: _embed(anchor) for cat, anchor in _CATEGORY_ANCHORS.items()}
        return self._anchor_vecs

    def _get_hazard_vecs(self) -> Dict[str, Optional[List[float]]]:
        if self._hazard_vecs is None:
            self._hazard_vecs = {name: _embed(text) for name, text in _HAZARD_ANCHORS.items()}
        return self._hazard_vecs

    def _momentum_vector(self) -> Optional[List[float]]:
        """Compute the weighted momentum vector from recent turns.

        More recent turns carry more weight (geometric decay ``MOMENTUM_DECAY``):
        turn[-1] weight=1.0, turn[-2] weight=0.80, turn[-3] weight=0.64, ...
        """
        turns = list(self._turns)
        if not turns:
            return None

        vecs_with_weights: List[Tuple[List[float], float]] = []
        weight = 1.0
        for turn in reversed(turns):
            if turn.embedding and len(turn.embedding) > 0:
                vecs_with_weights.append((turn.embedding, weight))
            weight *= MOMENTUM_DECAY

        if not vecs_with_weights:
            return None

        dim = len(vecs_with_weights[0][0])
        result = [0.0] * dim
        total_w = 0.0
        for vec, w in vecs_with_weights:
            if len(vec) == dim:
                total_w += w
                for i, v in enumerate(vec):
                    result[i] += v * w

        if total_w == 0:
            return None

        result = [v / total_w for v in result]

        # L2-normalize for cosine comparisons
        norm = math.sqrt(sum(x * x for x in result))
        if norm > 0:
            result = [x / norm for x in result]
        return result

    def _momentum_text(self) -> str:
        """Concatenation of recent turn texts for TF-IDF fallback."""
        return " ".join(t.text for t in self._turns)

    def compute_momentum(self) -> TrajectoryMomentum:
        """Compute category bias and hazard hints from the current window.

        Uses embedding cosine similarity when a backend is available, falling
        back to TF-IDF keyword overlap — never raises.
        """
        try:
            mvec = self._momentum_vector()
            mtext = self._momentum_text()

            # Category bias via cosine or TF-IDF
            category_bias: Dict[str, float] = {}
            for cat, anchor_text in _CATEGORY_ANCHORS.items():
                if mvec:
                    anchor_vec = self._get_anchor_vecs().get(cat)
                    score = _cosine(mvec, anchor_vec) if anchor_vec else 0.0
                else:
                    score = _tfidf_overlap(mtext, anchor_text)
                category_bias[cat] = round(score, 4)

            dominant_cat = max(category_bias, key=lambda c: category_bias[c])
            dominant_score = category_bias[dominant_cat]

            # Hazard hints
            hazard_hints: List[str] = []
            for hazard_name, anchor_text in _HAZARD_ANCHORS.items():
                if mvec:
                    hvec = self._get_hazard_vecs().get(hazard_name)
                    score = _cosine(mvec, hvec) if hvec else 0.0
                else:
                    score = _tfidf_overlap(mtext, anchor_text)
                if score >= HAZARD_THRESHOLD:
                    hazard_hints.append(
                        f"trajectory_{hazard_name}_proximity:{score:.2f}"
                    )

            return TrajectoryMomentum(
                category_bias=category_bias,
                hazard_hints=hazard_hints,
                dominant_category=dominant_cat,
                dominant_score=dominant_score,
            )
        except Exception as exc:
            logger.debug("trajectory_router: compute_momentum failed: %s", exc)
            return TrajectoryMomentum()

    def is_empty(self) -> bool:
        return len(self._turns) == 0

    def turn_count(self) -> int:
        return len(self._turns)


# --------------------------------------------------------------------------- #
# Session store
# --------------------------------------------------------------------------- #

class TrajectorySessionStore:
    """Thread-safe LRU-like store of active TrajectorySession objects.

    Sessions are evicted in LRU order when the store reaches ``max_sessions``.
    The store is in-memory only — no persistence — so it resets on process
    restart, which is fine for a sliding-window structure.
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._max = max_sessions
        self._sessions: "collections.OrderedDict[str, TrajectorySession]" = (
            collections.OrderedDict()
        )
        self._lock = threading.Lock()

    def get(self, session_id: str) -> TrajectorySession:
        with self._lock:
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
                return self._sessions[session_id]
            sess = TrajectorySession()
            self._sessions[session_id] = sess
            if len(self._sessions) > self._max:
                self._sessions.popitem(last=False)
            return sess

    def record_turn(self, session_id: str, text: str) -> None:
        self.get(session_id).record_turn(text)

    def get_momentum(self, session_id: str) -> TrajectoryMomentum:
        """Return momentum for a session (or a no-op momentum if new)."""
        with self._lock:
            if session_id not in self._sessions:
                return TrajectoryMomentum()
            self._sessions.move_to_end(session_id)
            sess = self._sessions[session_id]
        return sess.compute_momentum()

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)


# --------------------------------------------------------------------------- #
# Module-level singleton
# --------------------------------------------------------------------------- #

_STORE_LOCK = threading.Lock()
_STORE: Optional[TrajectorySessionStore] = None


def get_store() -> TrajectorySessionStore:
    """Return the process-level TrajectorySessionStore singleton."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = TrajectorySessionStore()
    return _STORE


def record_turn(session_id: str, text: str) -> None:
    """Record a turn in the session store (fail-soft convenience function)."""
    try:
        get_store().record_turn(session_id, text)
    except Exception as exc:
        logger.debug("trajectory_router: record_turn failed: %s", exc)


def get_momentum(session_id: str) -> TrajectoryMomentum:
    """Get routing momentum for a session (fail-soft convenience function)."""
    try:
        return get_store().get_momentum(session_id)
    except Exception as exc:
        logger.debug("trajectory_router: get_momentum failed: %s", exc)
        return TrajectoryMomentum()


def apply_momentum_bias(
    base_scores: Dict[str, float],
    momentum: TrajectoryMomentum,
    weight: float = 0.15,
) -> Dict[str, float]:
    """Blend trajectory momentum bias into benchmark routing category scores.

    ``base_scores`` is a ``{category: score}`` dict (from benchmark_routing).
    ``weight`` controls how strongly momentum biases the scores (default 0.15 —
    mild nudge, never overrides a strong base signal). Returns a new dict;
    never modifies the input.
    """
    if not momentum.category_bias or not base_scores:
        return dict(base_scores)
    result = {}
    for cat, base in base_scores.items():
        bias = momentum.category_bias.get(cat, 0.0)
        result[cat] = round(base + bias * weight, 4)
    return result
