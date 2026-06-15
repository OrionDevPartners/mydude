"""Benchmark-aware lead routing for the governed swarm (deterministic).

This module answers ONE question, cheaply and without any extra LLM call on the
hot path: *given a prompt + domain, which task category is this, and which of the
currently AVAILABLE providers is the benchmark "lead" for that category?*

It is a **tie-breaker**, never a router/override:
  * Classification is a deterministic keyword/domain heuristic (auditable, no
    inference), defaulting to ``general``. Only a bounded prefix of the prompt is
    inspected and the raw prompt is never echoed into the metadata.
  * Lead selection is ``argmax`` over each available provider's
    ``benchmark_profile[category]`` (declared per-provider in env_1 /
    config/providers.toml — never hardcoded here). Ties resolve to config order.
  * The result is pure metadata. The caller (MultiProviderLLM) uses it to give
    the lead a stronger specialization hint and a CAPPED, guarded weighting
    signal to the governed judge. It NEVER drops non-leads, skips the judge, or
    promotes a reply that fails the compliance/HR floors — those guards live at
    the call site and their outcome is recorded back onto the routing for audit.

Governance pillars honored: no stubs (real classification + selection), provider
-agnostic (no vendor names; driven by env_1 profiles), every inference still
governed (this adds no ungoverned output), dynamic/forward-compatible (categories
and profiles are data, not code).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# The benchmark categories the swarm reasons about. These are the keys providers
# advertise strengths for in their benchmark_profile (config/providers.toml). The
# list is the single source of truth shared by the classifier and the config
# assertions in the tests — extend it here when a new category is onboarded.
CATEGORIES: List[str] = [
    "coding",
    "agentic",
    "reasoning",
    "math",
    "long_context",
    "creative",
    "multilingual",
    "security",
    "frontend_uiux",
    "general",
]

# Bounded prompt inspection — classification never needs the whole prompt and we
# must not let a pathological input blow up the hot path.
_MAX_INSPECT_CHARS = 4000

# Keyword signals per category. Lower-cased, matched as substrings against the
# bounded, lower-cased prompt. frontend_uiux is intentionally rich because
# EXTREME UI/UX focus is a product requirement: a design/UI prompt must reliably
# route to the UI/UX lead. Order of CATEGORIES (not this dict) breaks ties.
_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "frontend_uiux": (
        "ui", "ux", "user interface", "user experience", "frontend", "front-end",
        "front end", "css", "tailwind", "design system", "component", "layout",
        "responsive", "accessibility", "a11y", "wcag", "figma", "wireframe",
        "mockup", "landing page", "animation", "transition", "typography",
        "color palette", "spacing", "react component", "styling", "visual design",
        "dashboard design", "interaction design", "design language",
    ),
    "coding": (
        "code", "function", "bug", "refactor", "implement", "compile", "api",
        "endpoint", "unit test", "stack trace", "exception", "class ", "method",
        "algorithm", "python", "typescript", "javascript", "rust", "golang",
        "sql query", "regex", "library", "dependency", "build error", "lint",
    ),
    "agentic": (
        "agent", "tool call", "workflow", "orchestrate", "multi-step", "pipeline",
        "automation", "autonomous", "plan and execute", "task decomposition",
        "browser automation", "scrape", "crawl", "schedule",
    ),
    "reasoning": (
        "reason", "analyze", "explain why", "trade-off", "tradeoff", "compare",
        "evaluate", "strategy", "decision", "root cause", "hypothesis", "logic",
        "implications", "pros and cons", "deduce", "infer",
    ),
    "math": (
        "calculate", "equation", "integral", "derivative", "probability",
        "matrix", "theorem", "proof", "arithmetic", "algebra", "geometry",
        "statistics", "optimi", "solve for", "formula",
    ),
    "long_context": (
        "summarize", "summarise", "long document", "entire codebase", "whole file",
        "across files", "transcript", "book", "report", "many pages",
        "large document", "full history", "synthesize across",
    ),
    "creative": (
        "write a story", "poem", "creative", "brainstorm", "tagline", "slogan",
        "marketing copy", "narrative", "screenplay", "lyrics", "fiction",
        "brand voice", "headline",
    ),
    "multilingual": (
        "translate", "translation", "in french", "in spanish", "in german",
        "in chinese", "in japanese", "localize", "localise", "multilingual",
        "language pair",
    ),
    "security": (
        "security", "vulnerability", "exploit", "cve", "threat model", "owasp",
        "penetration", "xss", "sql injection", "csrf", "auth bypass", "secrets",
        "encryption", "harden", "attack surface", "red team",
    ),
}

# Domain (jurisdiction slug) -> category nudge. The domain is operator-chosen and
# trustworthy, so a matching domain adds a strong, auditable signal on top of any
# keyword hits.
_DOMAIN_HINTS: Dict[str, str] = {
    "engineering": "coding",
    "frontend": "frontend_uiux",
    "design": "frontend_uiux",
    "security": "security",
    "legal": "reasoning",
    "finance": "reasoning",
    "medical": "reasoning",
    "marketing": "creative",
}

# Weight of a domain hint relative to a single keyword hit.
_DOMAIN_WEIGHT = 2


@dataclass
class BenchmarkRouting:
    """Auditable record of a benchmark-routing decision (pure metadata)."""
    category: str
    classification_signal: str
    lead_provider: Optional[str] = None
    lead_specialty: str = ""
    lead_score: Optional[float] = None
    scores_considered: Dict[str, float] = field(default_factory=dict)
    # Filled in by the call site AFTER governance scoring: whether the capped
    # lead bias was actually applied to the governed judge signal, and why.
    bias_applied: bool = False
    bias_reason: str = "not_evaluated"
    bias_delta: float = 0.0

    def mark_bias_applied(self, delta: float) -> None:
        self.bias_applied = True
        self.bias_reason = "lead passed compliance + HR guards"
        self.bias_delta = round(float(delta), 4)

    def mark_bias_suppressed(self, reason: str) -> None:
        self.bias_applied = False
        self.bias_reason = reason
        self.bias_delta = 0.0

    def to_dict(self) -> Dict:
        return {
            "category": self.category,
            "classification_signal": self.classification_signal,
            "lead_provider": self.lead_provider,
            "lead_specialty": self.lead_specialty,
            "lead_score": self.lead_score,
            "scores_considered": dict(self.scores_considered),
            "bias_applied": self.bias_applied,
            "bias_reason": self.bias_reason,
            "bias_delta": self.bias_delta,
        }


def _kw_matches(kw: str, text: str) -> bool:
    """Deterministic keyword match against the lower-cased prompt.

    Multi-word / hyphenated / slashed phrases (e.g. "user interface",
    "front-end", "sql injection") match as plain substrings. Single tokens match
    at a WORD START so short tokens like "ui"/"ux" don't fire inside unrelated
    words (e.g. "build"), while deliberate prefixes like "optimi" still catch
    "optimize"/"optimization"."""
    if " " in kw or "-" in kw or "/" in kw:
        return kw in text
    return re.search(r"\b" + re.escape(kw), text) is not None


def classify_category(prompt: str, domain: str = "general") -> Tuple[str, str]:
    """Deterministically map (prompt, domain) -> (category, short_signal).

    Scores each category by counting keyword substring hits in a bounded,
    lower-cased prefix of the prompt, plus a domain nudge. Returns ``general``
    when nothing matches. ``signal`` is a compact, auditable reason that NEVER
    contains a raw prompt excerpt — only matched tokens / the domain.
    """
    text = (prompt or "")[:_MAX_INSPECT_CHARS].lower()
    domain_slug = (domain or "general").strip().lower()

    scores: Dict[str, int] = {c: 0 for c in CATEGORIES if c != "general"}
    matched: Dict[str, List[str]] = {c: [] for c in scores}

    for category, kws in _KEYWORDS.items():
        for kw in kws:
            if _kw_matches(kw, text):
                scores[category] += 1
                matched[category].append(kw.strip())

    domain_hint = _DOMAIN_HINTS.get(domain_slug)
    if domain_hint and domain_hint in scores:
        scores[domain_hint] += _DOMAIN_WEIGHT

    best = max(scores, key=lambda c: scores[c]) if scores else "general"
    if not scores or scores[best] == 0:
        signal = (
            f"domain={domain_slug}; no category keywords matched -> default general"
        )
        return "general", signal

    parts: List[str] = []
    if domain_hint == best:
        parts.append(f"domain={domain_slug}")
    hits = matched[best][:5]
    if hits:
        parts.append("matched: " + ", ".join(hits))
    signal = "; ".join(parts) or f"category={best}"
    return best, signal


def _category_strength(profile: Dict, category: str) -> float:
    """Provider's strength for a category, falling back to its 'general' score.

    Profiles are operator-declared (env_1); a provider that doesn't list a
    category is judged on its general score so selection never crashes on a
    sparse profile (forward-compatible with new categories)."""
    if not isinstance(profile, dict):
        return 0.0
    val = profile.get(category)
    if val is None:
        val = profile.get("general", 0.0)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def select_lead(
    category: str,
    candidates: List[Tuple[str, str, Dict]],
) -> Tuple[Optional[str], str, Optional[float], Dict[str, float]]:
    """Pick the benchmark lead for ``category`` from AVAILABLE candidates.

    ``candidates`` is ``[(provider_key, specialty, benchmark_profile), ...]`` in
    config (declared) order — typically built from the swarm's currently
    available adapters so the lead is always a provider that can actually answer.

    Returns ``(lead_provider, lead_specialty, lead_score, scores_considered)``.
    With no candidates the lead is ``None`` (caller simply applies no bias).
    Ties resolve to the first candidate in declared order (stable, auditable).
    """
    scores: Dict[str, float] = {}
    specialties: Dict[str, str] = {}
    for key, specialty, profile in candidates:
        scores[key] = round(_category_strength(profile, category), 4)
        specialties[key] = specialty or ""

    if not scores:
        return None, "", None, {}

    # max() over the dict preserves first-seen (declared) order on ties.
    lead = max(scores, key=lambda k: scores[k])
    return lead, specialties.get(lead, ""), scores[lead], scores


def route(
    prompt: str,
    domain: str,
    candidates: List[Tuple[str, str, Dict]],
    *,
    session_id: Optional[str] = None,
    momentum_weight: float = 0.15,
) -> BenchmarkRouting:
    """Classify + select lead in one call, returning an auditable routing record.

    Parameters
    ----------
    prompt : the task prompt (bounded to _MAX_INSPECT_CHARS internally).
    domain : operator-declared domain hint (maps to a category nudge).
    candidates : ``[(provider_key, specialty, benchmark_profile), ...]`` in
        declared order — built from the swarm's available adapters.
    session_id : optional conversation session key. When provided, the
        trajectory router's momentum vector is retrieved and blended into
        the keyword category scores with ``momentum_weight`` (default 0.15).
        Momentum bias is additive and never overrides a strong base keyword
        signal — the final category is still the argmax of the blended scores.
        Fails-soft: if the trajectory router is unavailable or returns no
        momentum, routing is unaffected.
    momentum_weight : fraction of the momentum bias blended into the base
        keyword scores. Range [0, 1]; capped at 0.3 to prevent momentum from
        dominating deterministic keyword classification. Default: 0.15.
    """
    category, signal = classify_category(prompt, domain)

    # ── Trajectory momentum bias ────────────────────────────────────────────
    # Blend conversational momentum into the keyword scores so multi-turn
    # sessions steer toward the model that leads in the user's ongoing domain.
    trajectory_applied = False
    if session_id is not None:
        try:
            from src.swarm.trajectory_router import get_momentum, apply_momentum_bias
            momentum = get_momentum(session_id)
            if momentum.dominant_score > 0.0:
                weight = max(0.0, min(0.3, momentum_weight))
                keyword_scores: Dict[str, float] = {
                    c: 0.0 for c in CATEGORIES if c != "general"
                }
                keyword_scores[category] = 1.0
                biased = apply_momentum_bias(keyword_scores, momentum, weight=weight)
                best_biased = max(biased, key=lambda c: biased[c]) if biased else category
                if best_biased != category:
                    signal = (
                        f"{signal}; trajectory_bias={momentum.dominant_category}"
                        f"(w={weight:.2f}) -> {best_biased}"
                    )
                    category = best_biased
                trajectory_applied = True
        except Exception as _tb_exc:
            import logging as _logging
            _logging.getLogger(__name__).debug(
                "benchmark_routing: trajectory bias failed (skipped): %s", _tb_exc
            )

    lead, specialty, lead_score, scores = select_lead(category, candidates)
    result = BenchmarkRouting(
        category=category,
        classification_signal=signal,
        lead_provider=lead,
        lead_specialty=specialty,
        lead_score=lead_score,
        scores_considered=scores,
    )
    result.trajectory_bias_applied = trajectory_applied
    return result
