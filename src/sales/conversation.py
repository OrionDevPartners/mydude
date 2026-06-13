"""Governed sales-conversation engine.

A DETERMINISTIC phase engine drives every conversation; the LLM swarm only
phrases persuasive prose (opener / closing), and that phrasing is governance-
scored and gated before it reaches a prospect. Governance-critical behaviour is
never delegated to a model:

  * Question cap        — enforced as a hard counter (deterministic).
  * AI disclosure       — when the prospect asks if they are talking to a bot,
                          the exact operator-approved disclosure is returned
                          verbatim, every time (regex-detected, deterministic).
  * Qualification gate  — decided by counting substantive prospect answers
                          against an operator threshold (deterministic).
  * Booking             — routed through the capability broker (calendly_book)
                          so it is policy-gated and recorded in the audit trail.

Phases: opener -> qualify -> close -> booked | ended.

Governed-degraded fallback: when the swarm is unavailable or its output fails
the compliance/hallucination gate, the bot falls back to the operator's
pre-approved script text (the live approved prompt), marks the turn ``degraded``
in the transcript, and continues — never emitting raw, ungoverned model output.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Governance gate thresholds for swarm-phrased prose.
_CS_FLOOR = 50          # minimum per-provider compliance score (0-100)
_HR_CEIL = 0.6          # maximum per-provider hallucination risk (0-1)

_DEFAULT_DISCLOSURE = (
    "Yes — I'm an AI assistant, not a human. I'm happy to help, and I can "
    "connect you with a member of our team at any time."
)

# Deterministic detection of "are you an AI/bot/human?" style questions.
_AI_QUESTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bare you (a |an )?(ai|a\.?i\.?|bot|robot|human|machine|automated|chat\s?bot|real|person)\b",
        r"\bis this (a |an )?(ai|a\.?i\.?|bot|robot|human|machine|automated|chat\s?bot|real person|real human|person)\b",
        r"\b(am i|are we) (talking|chatting|speaking|texting) (to|with) (a |an )?(ai|bot|robot|human|machine|real person|person)\b",
        r"\bare you (a )?real (person|human|one)\b",
        r"\b(chat\s?bot|a\.i\.)\b",
        r"\b(human or|or a robot|or a bot|or an ai|or a machine|person or a)\b",
        r"\bis this (a )?(real )?(person|human)\b",
        r"\bam i speaking (to|with) (a |an )?(real )?(person|human|bot|ai)\b",
    ]
]

# Deterministic negative-answer markers for the qualification gate.
_NEGATIVE_MARKERS = [
    "not interested", "no budget", "not a fit", "not now", "maybe later",
    "too expensive", "no thanks", "no thank you", "don't", "do not", "won't",
    "will not", "never", "not really", "nope", "nah", "pass", "go away",
    "stop", "unsubscribe", "remove me",
]


class SalesConfigError(RuntimeError):
    """Raised when a bot is not (fully) configured for sales mode."""


class SalesConversationError(RuntimeError):
    """Raised on an invalid conversation operation (bad id / wrong state)."""


# --------------------------------------------------------------------------- #
# Config loading / validation
# --------------------------------------------------------------------------- #

def load_sales_config(bot) -> Dict[str, Any]:
    """Validate and normalise a bot's operator-configured sales script.

    Fails loud (SalesConfigError) when the required pieces are missing — there
    is no implicit default script, the operator must configure sales mode.
    """
    cfg = bot.sales_config or {}
    opener = (cfg.get("opener") or "").strip()
    questions = [q.strip() for q in (cfg.get("qualification_questions") or [])
                 if isinstance(q, str) and q.strip()]
    closing = (cfg.get("closing_prompt") or "").strip()

    missing = []
    if not opener:
        missing.append("opener")
    if not questions:
        missing.append("qualification_questions")
    if not closing:
        missing.append("closing_prompt")
    if missing:
        raise SalesConfigError(
            "Bot sales mode is not fully configured. Missing: "
            + ", ".join(missing)
            + ". Configure the bot's sales script before starting a conversation."
        )

    # Question cap: never exceed the number of authored questions.
    try:
        max_q = int(cfg.get("max_questions") or len(questions))
    except (TypeError, ValueError):
        max_q = len(questions)
    max_q = max(1, min(max_q, len(questions)))

    try:
        threshold = int(cfg.get("qualify_threshold") or max_q)
    except (TypeError, ValueError):
        threshold = max_q
    threshold = max(1, min(threshold, max_q))

    disclosure = (cfg.get("disclosure") or _DEFAULT_DISCLOSURE).strip()
    event_type_uri = (cfg.get("event_type_uri") or "").strip() or None

    return {
        "opener": opener,
        "qualification_questions": questions,
        "closing_prompt": closing,
        "max_questions": max_q,
        "qualify_threshold": threshold,
        "disclosure": disclosure,
        "event_type_uri": event_type_uri,
        "product": (cfg.get("product") or "").strip(),
        "tone": (cfg.get("tone") or "warm, concise, professional").strip(),
    }


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #

def asks_if_ai(text: str) -> bool:
    """True when the prospect is asking whether they're talking to an AI/bot."""
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(p.search(low) for p in _AI_QUESTION_PATTERNS)


def _is_negative_answer(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True  # an empty answer does not count toward qualification
    if low in ("no", "n"):
        return True
    return any(m in low for m in _NEGATIVE_MARKERS)


def _now() -> str:
    return datetime.utcnow().isoformat()


# Section headers used by the governed judge synthesis. We surface ONLY the
# RESULT prose to a prospect — never the internal scaffolding (ARTIFACTS /
# CHECKS / RISKS / CAPABILITIES / COMPRESSED_HANDOFF / MODE / WARNING).
_JUDGE_SECTIONS = (
    "ARTIFACTS", "CHECKS", "RISKS", "CAPABILITIES",
    "COMPRESSED_HANDOFF", "MODE", "WARNING",
)
_RESULT_RE = re.compile(
    r"(?ims)^\s*RESULT\s*:?\s*\n?(.*?)"
    r"(?=^\s*(?:ARTIFACTS|CHECKS|RISKS|CAPABILITIES|COMPRESSED_HANDOFF|MODE|WARNING)\b|\Z)"
)


def _extract_prose(merged: str) -> str:
    """Pull the clean prospect-facing prose out of a judge synthesis.

    The swarm's judge returns a structured ``RESULT/ARTIFACTS/CHECKS/...``
    block. Only the RESULT body is fit to send to a prospect. Returns an empty
    string when the text is structured scaffolding with no usable RESULT, so the
    caller treats it as a gate miss and falls back to the operator script.
    """
    if not merged:
        return ""
    text = merged.strip()
    m = _RESULT_RE.search(text)
    if m:
        body = m.group(1).strip()
    elif any(h in text for h in _JUDGE_SECTIONS):
        # Scaffolding without a parseable RESULT — never emit it raw.
        return ""
    else:
        body = text
    # Strip a single layer of wrapping quotes the judge sometimes adds.
    body = body.strip()
    if len(body) >= 2 and body[0] in "\"'" and body[-1] == body[0]:
        body = body[1:-1].strip()
    return body


# --------------------------------------------------------------------------- #
# Governed phrasing (swarm-scored, gated, with fail-safe to operator script)
# --------------------------------------------------------------------------- #

async def _govern_phrase(base_text: str, intent: str, cfg: Dict[str, Any],
                         history: str) -> Tuple[str, Optional[Dict], bool]:
    """Phrase ``base_text`` naturally via the governed LLM swarm.

    Returns ``(text, governance, degraded)``. The swarm's output is used ONLY
    when it clears the compliance/hallucination gate; otherwise the operator's
    pre-approved ``base_text`` is returned and the turn is marked degraded.
    Never raises and never emits ungoverned output.
    """
    try:
        from src.swarm.llm_multi import MultiProviderLLM
        system = (
            "You are a sales development representative for an AI business "
            "automation platform. Rephrase the operator's approved line so it "
            f"sounds natural and {cfg.get('tone') or 'warm and concise'}. "
            "Do NOT invent facts, prices, features, guarantees, or commitments. "
            "Keep it to one or two short sentences. Never claim to be human. "
            "Preserve the intent exactly."
        )
        product = cfg.get("product")
        user = (
            (f"Product/offer context: {product}\n" if product else "")
            + (f"Recent conversation:\n{history}\n" if history else "")
            + f"Intent: {intent}\n"
            + f"Operator-approved line to rephrase faithfully:\n{base_text}"
        )
        out = await MultiProviderLLM().call_team(system, user)
        prose = _extract_prose(out.get("merged") or "")
        cs = out.get("compliance_scores") or {}
        hr = out.get("hallucination_risks") or {}
        gated_ok = (
            bool(prose)
            and bool(cs)
            and min(cs.values()) >= _CS_FLOOR
            and (not hr or max(hr.values()) <= _HR_CEIL)
        )
        if gated_ok:
            return prose, {"compliance_scores": cs, "hallucination_risks": hr}, False
        logger.info("Sales phrasing fell back to operator script (gate not met): "
                    "cs=%s hr=%s", cs, hr)
    except Exception as e:
        logger.warning("Sales phrasing swarm call failed, using operator script: %s", e)
    return base_text, None, True


# --------------------------------------------------------------------------- #
# Transcript helpers
# --------------------------------------------------------------------------- #

def _append(conv, role: str, text: str, phase: str,
            governance: Optional[Dict] = None, degraded: bool = False) -> Dict:
    entry = {
        "role": role,
        "text": text,
        "phase": phase,
        "governance": governance,
        "degraded": degraded,
        "ts": _now(),
    }
    transcript = list(conv.transcript or [])
    transcript.append(entry)
    conv.transcript = transcript
    return entry


def _current_question_index(conv) -> int:
    """How many qualification questions the bot has asked so far."""
    return conv.questions_asked or 0


def _count_positive_answers(conv) -> int:
    positives = 0
    for entry in (conv.transcript or []):
        if entry.get("role") == "prospect" and entry.get("phase") == "qualify":
            if not _is_negative_answer(entry.get("text", "")):
                positives += 1
    return positives


# --------------------------------------------------------------------------- #
# Public engine: start + message
# --------------------------------------------------------------------------- #

async def start_conversation(db, bot, prospect_name: str = "",
                             prospect_contact: str = "") -> Any:
    """Create a conversation and emit the opener + first qualification question.

    ``db`` is an open SQLAlchemy session; the caller owns commit/close.
    Raises SalesConfigError if the bot's sales mode is not configured.
    """
    from src.models import SalesConversation
    from src.swarm.integrations import audit_capability

    cfg = load_sales_config(bot)

    conv = SalesConversation(
        bot_id=bot.id,
        prospect_name=(prospect_name or "").strip() or None,
        prospect_contact=(prospect_contact or "").strip() or None,
        phase="opener",
        status="active",
        qualified=False,
        questions_asked=0,
        disclosed_ai=False,
        transcript=[],
    )
    db.add(conv)
    db.flush()  # assign conv.id without ending the caller's transaction

    # Opener (governed phrasing, operator script as fail-safe).
    opener_text, gov, degraded = await _govern_phrase(
        cfg["opener"], "greet the prospect and open the conversation", cfg, "")
    _append(conv, "bot", opener_text, "opener", gov, degraded)

    # First qualification question (verbatim — governance-critical, not LLM-phrased).
    first_q = cfg["qualification_questions"][0]
    _append(conv, "bot", first_q, "qualify")
    conv.questions_asked = 1
    conv.phase = "qualify"

    audit_capability(
        "sales_conversation",
        target=conv.prospect_name or str(conv.id),
        backend="sales",
        status="ok",
        detail=f"started conversation id={conv.id} for bot={bot.id}",
        source="sales",
    )
    db.flush()
    return conv


async def handle_message(db, conv, bot, prospect_message: str) -> Dict[str, Any]:
    """Advance the conversation by one prospect turn.

    Returns a dict describing the bot's reply(ies) and the new conversation
    state. ``db`` is an open session owned by the caller.
    """
    from src.swarm.integrations import audit_capability

    if conv.status not in ("active",):
        raise SalesConversationError(
            f"Conversation {conv.id} is '{conv.status}' and cannot accept more messages."
        )

    cfg = load_sales_config(bot)
    msg = (prospect_message or "").strip()
    if not msg:
        raise SalesConversationError("A prospect message is required.")

    _append(conv, "prospect", msg, conv.phase or "qualify")

    replies: List[Dict[str, Any]] = []

    # ---- 1. AI-disclosure gate (deterministic, always honoured) -------------
    if asks_if_ai(msg):
        entry = _append(conv, "bot", cfg["disclosure"], conv.phase or "qualify")
        conv.disclosed_ai = True
        replies.append(entry)
        audit_capability(
            "sales_conversation", target=conv.prospect_name or str(conv.id),
            backend="sales", status="ok",
            detail=f"AI disclosure issued for conversation id={conv.id}", source="sales",
        )
        # Re-ask the pending question so the flow resumes where it paused.
        pending_idx = _current_question_index(conv) - 1
        questions = cfg["qualification_questions"]
        if conv.phase == "qualify" and 0 <= pending_idx < len(questions):
            replies.append(_append(conv, "bot", questions[pending_idx], "qualify"))
        db.flush()
        return _state(conv, replies)

    # ---- 2. Qualify phase: ask the next question or move to close -----------
    if conv.phase == "qualify":
        asked = _current_question_index(conv)
        questions = cfg["qualification_questions"]
        cap = cfg["max_questions"]
        if asked < cap and asked < len(questions):
            # Ask the next question (verbatim, capped).
            next_q = questions[asked]
            replies.append(_append(conv, "bot", next_q, "qualify"))
            conv.questions_asked = asked + 1
            db.flush()
            return _state(conv, replies)
        # Question cap reached — evaluate qualification deterministically.
        conv.phase = "close"

    # ---- 3. Close phase: qualify decision + booking -------------------------
    if conv.phase == "close":
        positives = _count_positive_answers(conv)
        qualified = positives >= cfg["qualify_threshold"]
        conv.qualified = qualified

        if not qualified:
            decline = (
                "Thanks so much for your time — it sounds like this might not be "
                "the right fit right now. I'll leave the door open if anything changes."
            )
            text, gov, degraded = await _govern_phrase(
                decline, "politely close a non-qualified prospect", cfg,
                _recent_history(conv))
            replies.append(_append(conv, "bot", text, "close", gov, degraded))
            conv.status = "disqualified"
            conv.phase = "ended"
            audit_capability(
                "sales_conversation", target=conv.prospect_name or str(conv.id),
                backend="sales", status="ok",
                detail=f"conversation id={conv.id} disqualified "
                       f"({positives}/{cfg['qualify_threshold']} positive)",
                source="sales",
            )
            db.flush()
            return _state(conv, replies)

        # Qualified — close with the operator's prompt, then book.
        close_text, gov, degraded = await _govern_phrase(
            cfg["closing_prompt"], "close the qualified prospect toward booking a meeting",
            cfg, _recent_history(conv))
        replies.append(_append(conv, "bot", close_text, "close", gov, degraded))

        booking = await _book_meeting(conv, cfg)
        if booking.get("ok"):
            url = booking.get("booking_url")
            conv.booking_url = url
            conv.booking_ref = booking.get("booking_ref")
            book_msg = (
                f"Great — you can grab a time that works for you here: {url}"
            )
            replies.append(_append(conv, "bot", book_msg, "booked"))
            conv.status = "booked"
            conv.phase = "booked"
        else:
            # Fail loud: surface the real reason, never fabricate a link.
            err = booking.get("error") or "Booking is unavailable."
            followup = (
                "I'd love to get a meeting on the calendar — our scheduling link "
                "isn't available this moment, so someone from our team will follow "
                "up shortly to lock in a time."
            )
            replies.append(_append(conv, "bot", followup, "close",
                                   governance=None, degraded=True))
            # Keep the conversation active so the operator can retry once Calendly
            # is connected; the error is recorded in the capability audit trail.
            conv.status = "active"
            logger.warning("Sales booking failed for conversation %s: %s", conv.id, err)

        db.flush()
        return _state(conv, replies, booking_error=None if booking.get("ok") else booking.get("error"))

    # Any other phase (booked / ended) shouldn't reach here due to the status guard.
    db.flush()
    return _state(conv, replies)


async def _book_meeting(conv, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Route the booking through the capability broker (policy-gated + audited)."""
    from src.swarm.broker import CapabilityBroker
    from src.swarm.integrations import Integrations
    from src.swarm.policy import PolicyEngine

    broker = CapabilityBroker(PolicyEngine(), Integrations())
    params: Dict[str, Any] = {
        "conversation_id": conv.id,
        "prospect": conv.prospect_name or str(conv.id),
        "source": "sales",
    }
    if cfg.get("event_type_uri"):
        params["event_type_uri"] = cfg["event_type_uri"]

    result = await broker.request("calendly_book", params)
    if not result.ok:
        return {"ok": False, "error": result.decision.reason}
    try:
        return json.loads(result.output)
    except Exception:
        return {"ok": False, "error": "Booking returned an unparseable response."}


def _recent_history(conv, limit: int = 6) -> str:
    entries = (conv.transcript or [])[-limit:]
    lines = []
    for e in entries:
        who = "Prospect" if e.get("role") == "prospect" else "Bot"
        lines.append(f"{who}: {e.get('text', '')}")
    return "\n".join(lines)


def _state(conv, replies: List[Dict[str, Any]],
           booking_error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "conversation_id": conv.id,
        "phase": conv.phase,
        "status": conv.status,
        "qualified": bool(conv.qualified),
        "questions_asked": conv.questions_asked,
        "disclosed_ai": bool(conv.disclosed_ai),
        "booking_url": conv.booking_url,
        "replies": replies,
        "booking_error": booking_error,
    }
