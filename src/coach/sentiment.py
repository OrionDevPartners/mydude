"""Text sentiment via the governed LLM swarm.

A separate, always-available signal source distinct from the specialized Hume
emotion provider. Output is governed (each reply scored for compliance +
hallucination by the swarm). Fails loud when no LLM provider is configured — it
never returns a fabricated neutral score. It describes ONLY the emotional tone of
the text; it does not invent facts about the person.
"""
import json
import logging
import re

from src.coach.llm import call_team_sync  # noqa: F401 (re-exported failure type below)

logger = logging.getLogger(__name__)

_SYS = (
    "You are a precise text-sentiment analyzer. Read the user's text and return "
    "ONLY a compact JSON object with keys: "
    "valence (float -1..1; -1 very negative, +1 very positive), "
    "arousal (float 0..1; 0 calm, 1 highly activated), "
    "label (one lowercase word naming the dominant emotion), "
    "summary (<=120 chars, a neutral observation of the emotional tone). "
    "Describe ONLY the emotional tone of the text — never invent facts about the "
    "person. Output JSON only: no prose, no code fences."
)


def _extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _clampf(v, lo, hi, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def analyze_text_sentiment(text, strict_private=None):
    """Return ``{valence, arousal, label, summary, compliance_scores,
    hallucination_risks}``.

    ``strict_private`` pins inference to LOCAL providers so the raw text never
    egresses to a cloud LLM (Private-Mode). When ``None`` it is read from the
    ``COACH_STRICT_PRIVATE`` setting, mirroring ``coach.ask``/``reflection``.

    Raises ``CoachLLMUnavailable`` when no provider is configured (or, in strict
    mode, no LOCAL provider) and ``ValueError`` on empty input or unparseable
    model output (fail loud).
    """
    if not text or not text.strip():
        raise ValueError("Cannot analyze empty text.")
    if strict_private is None:
        from src.web.settings_store import get_setting
        strict_private = (get_setting("COACH_STRICT_PRIVATE", "0") or "0") == "1"
    result = call_team_sync(_SYS, text.strip()[:4000], strict_private=strict_private)
    parsed = _extract_json(result.get("merged") or "")
    if not parsed:
        raise ValueError("Sentiment model returned an unparseable result.")

    label = parsed.get("label")
    summary = parsed.get("summary")
    return {
        "valence": _clampf(parsed.get("valence"), -1.0, 1.0, 0.0),
        "arousal": _clampf(parsed.get("arousal"), 0.0, 1.0, None),
        "label": (str(label).strip().lower()[:80] or "neutral")
                 if label is not None else "neutral",
        "summary": str(summary).strip()[:240] if summary is not None else None,
        "compliance_scores": result.get("compliance_scores"),
        "hallucination_risks": result.get("hallucination_risks"),
    }
