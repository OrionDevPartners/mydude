"""Empathetic life-coach: grounded, cited answers over the memory graph.

Governance-first:
  - Retrieval-grounded: every answer is built ONLY from memories recalled from the
    substrate (mood + behavior + finance nodes). Recalled items are enumerated
    [M1]..[Mn] and the model is instructed to cite them and use no other facts.
  - Fail loud: when recall returns nothing we short-circuit BEFORE calling any LLM
    and return ``insufficient_data`` — we never fabricate a pattern or platitude.
  - Private-Mode for inference: when ``COACH_STRICT_PRIVATE`` is on, the swarm is
    pinned to LOCAL providers so recalled (sensitive) content is never sent to a
    cloud model. Otherwise the recalled summaries DO reach the configured cloud
    providers — a documented second egress the operator can disable.
"""
import logging

from src.models import CoachAuditLog
from src.coach.llm import call_team_sync, CoachLLMUnavailable

logger = logging.getLogger(__name__)

_ASK_SYS = (
    "You are an empathetic, grounded life coach inside a governance-first "
    "platform. You speak warmly and concisely. CRITICAL RULES:\n"
    "1. Ground every observation ONLY in the numbered memories provided. Cite "
    "them inline as [M1], [M2], etc.\n"
    "2. Never invent facts, events, diagnoses, or patterns that are not supported "
    "by the cited memories. If the memories are thin or inconclusive, say so "
    "honestly rather than guessing.\n"
    "3. Be supportive but non-clinical; you are a coach, not a therapist or "
    "medical professional. Do not diagnose.\n"
    "4. End with ONE concrete, small, optional next step the person could take."
)


def _audit(db, status, detail):
    db.add(CoachAuditLog(action="ask", status=status, source="coach", detail=detail))
    db.commit()


def ask(db, question, top_k=8, strict_private=None):
    """Answer a coaching question grounded in recalled memory. Fail loud when
    there is nothing to ground on or no LLM provider is configured."""
    if not question or not question.strip():
        raise ValueError("A question is required.")

    from src.memory.substrate import get_substrate
    memories = get_substrate().recall(question.strip(), top_k=top_k, min_confidence=0.0)

    citations = [
        {
            "ref": "M%d" % idx,
            "memory_id": getattr(m, "memory_id", None),
            "content": getattr(m, "content", "") or "",
            "category": getattr(m, "category", None),
        }
        for idx, m in enumerate(memories, 1)
    ]

    if not citations:
        _audit(db, "insufficient_data", "No grounded memories for: %s" % question[:120])
        return {
            "status": "insufficient_data",
            "answer": None,
            "citations": [],
            "message": (
                "I don't have enough recorded signals to answer that yet. Capture "
                "a few mood entries or let behavior signals accrue, then ask again."
            ),
        }

    if strict_private is None:
        from src.web.settings_store import get_setting
        strict_private = (get_setting("COACH_STRICT_PRIVATE", "0") or "0") == "1"

    context = "\n".join(
        "[%s] (%s) %s" % (c["ref"], c["category"] or "memory", c["content"])
        for c in citations
    )
    user = (
        "Question: %s\n\n"
        "Grounded memories you may cite as [M#] (use no fact not present here):\n%s"
        % (question.strip(), context)
    )

    try:
        result = call_team_sync(_ASK_SYS, user, strict_private=strict_private)
    except CoachLLMUnavailable as e:
        _audit(db, "no_provider", str(e))
        raise

    _audit(db, "ok",
           "Answered with %d citation(s), strict_private=%s." % (len(citations), strict_private))
    return {
        "status": "ok",
        "answer": result.get("merged") or "",
        "citations": citations,
        "strict_private": strict_private,
        "compliance_scores": result.get("compliance_scores"),
        "hallucination_risks": result.get("hallucination_risks"),
    }
