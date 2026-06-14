"""Governed per-turn conversation loop for phone calls (Task #66).

Every spoken bot reply is governed exactly like a sales phrase: the LLM swarm
drafts the line, it is compliance/hallucination scored, and only output that
clears the gate is spoken. Output that misses the gate falls back to an
operator-aligned line built deterministically from the bot's own (operator
-authored) configuration and is marked ``degraded`` — never raw ungoverned text
and never a fabricated claim (governance pillars #1 + #4).

Unlike sales phrasing, each call turn also persists a ``DecisionTrace`` so the
spoken conversation is auditable turn-by-turn, matching every other governed
agent path. We use the lightweight governed-phrase seam (not the full
WaveOrchestrator) because a live phone turn must answer within the provider's
gather timeout; the sales conversation engine makes the same trade-off.

Speech recognition is the telephony provider's (Twilio ``<Gather input=speech>``);
the reply is synthesized through the avatar voice layer (ElevenLabs) and parked
in the audio store for ``<Play>`` playback.
"""
import json
import logging
import re
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

# Gate thresholds mirror the sales conversation engine for consistency.
_CS_FLOOR = 50          # minimum per-provider compliance score (0-100)
_HR_CEIL = 0.6          # maximum per-provider hallucination risk (0-1)

_MAX_TURNS = 12         # hard cap on spoken exchanges before a graceful wrap-up
_MAX_REPLY_CHARS = 600  # keep spoken replies short; long synthesis is unnatural

_JUDGE_SECTIONS = ("RESULT:", "ARTIFACTS:", "CHECKS:", "RISKS:", "ASSUMPTIONS:")
_RESULT_RE = re.compile(r"RESULT:\s*(.+?)(?:\n[A-Z]+:|\Z)", re.DOTALL)

_GOODBYE_RE = re.compile(
    r"\b(bye|goodbye|good bye|hang up|that'?s all|nothing else|we'?re done|"
    r"no thanks?,? bye|talk later|have a good (day|one))\b",
    re.IGNORECASE,
)


def _now():
    return datetime.utcnow().isoformat()


def _extract_prose(merged):
    """Pull caller-facing prose out of a judge synthesis (see sales analog)."""
    if not merged:
        return ""
    text = merged.strip()
    m = _RESULT_RE.search(text)
    if m:
        body = m.group(1).strip()
    elif any(h in text for h in _JUDGE_SECTIONS):
        # Structured scaffolding with no parseable RESULT — never speak it raw.
        return ""
    else:
        body = text
    return body.strip()


def _disclosure(bot):
    """Required AI-disclosure clause. A compliance constant, not a placeholder."""
    name = (bot.name or "MyDude").strip()
    return ("Quick heads-up: I'm an AI assistant calling on behalf of %s." % name)


def _persona(bot):
    """Operator-authored persona/goal context used to steer (not invent) replies."""
    bits = []
    ident = bot.identity_schema if isinstance(getattr(bot, "identity_schema", None), dict) else {}
    for k in ("persona", "role", "voice", "tone"):
        v = ident.get(k)
        if v:
            bits.append("%s: %s" % (k, v))
    if getattr(bot, "goal", None):
        bits.append("goal: %s" % bot.goal)
    return " | ".join(bits)


def _approved_opener(bot):
    """Deterministic, operator-aligned opener (the degraded-path fallback).

    Built from operator-authored fields (the bot's name + goal) plus the required
    AI disclosure. This is approved content (the operator wrote the bot), so it is
    a legitimate fail-safe — not a hardcoded pitch.
    """
    disclosure = _disclosure(bot)
    goal = (getattr(bot, "goal", None) or "").strip()
    if goal:
        return "%s I'm calling about %s. Is now a good time?" % (disclosure, goal)
    return "%s How can I help you today?" % disclosure


def _safe_deferral(bot):
    """Non-fabricating fail-safe reply when a turn misses the governance gate.

    A refusal to guess (the governed "I don't know" path) — it asserts no facts,
    so it is safe to speak when the swarm output cannot be trusted.
    """
    sc = bot.sales_config if isinstance(getattr(bot, "sales_config", None), dict) else {}
    closing = (sc.get("closing") or "").strip()
    if closing:
        return closing
    return ("I'd rather not guess on that — let me have a teammate follow up with "
            "the exact details. What's the best way to reach you?")


def _build_prompts(bot, caller_text, history, intent):
    """Build (system, user, base_text) for the governed turn."""
    persona = _persona(bot)
    disclosure = _disclosure(bot)
    system = (
        "You are a bot conducting a live phone call. Speak in short, natural, "
        "spoken sentences (1-3 sentences, no markdown, no lists). Never invent "
        "facts, prices, availability, or commitments — if you don't know, say so "
        "and offer a human follow-up. Always disclose you are an AI if asked. "
        "Stay strictly on the caller's intent and the bot's goal.\n"
        "Bot identity: %s\n"
        "Required disclosure (use verbatim on the opener): %s"
    ) % (persona or "(general assistant)", disclosure)

    convo = "\n".join(
        "%s: %s" % (t.get("role", "?"), t.get("text", "")) for t in (history or [])[-8:]
    )
    if intent == "opener":
        base_text = _approved_opener(bot)
        user = (
            "Open the call. Greet briefly, give the required AI disclosure verbatim, "
            "state why you're calling (the goal), and ask if now is a good time. "
            "Keep it to 2 short sentences."
        )
    else:
        base_text = _safe_deferral(bot)
        user = (
            "Conversation so far:\n%s\n\nThe caller just said: \"%s\"\n\n"
            "Reply in 1-3 short spoken sentences. If you cannot answer truthfully, "
            "offer a human follow-up instead of guessing."
        ) % (convo or "(call just connected)", (caller_text or "").strip())
    return system, user, base_text


async def _govern_reply(system, user, base_text):
    """Draft -> gate -> (text, governance, degraded). Mirrors sales _govern_phrase.

    Returns the operator-aligned ``base_text`` marked ``degraded=True`` whenever
    no provider is configured, the swarm errors, the prose is empty, or the
    compliance/hallucination gate is missed — so a real, governed (or explicitly
    degraded) line is always produced; output is never raw or fabricated.
    """
    governance = {"cs": None, "hr": None, "providers": 0}
    try:
        from src.swarm.llm_multi import MultiProviderLLM
        result = await MultiProviderLLM().call_team(system, user, domain="telephony")
    except Exception as e:  # noqa: BLE001 — degrade, never crash a live call
        logger.warning("[TELEPHONY] swarm call failed, degrading: %s", e)
        return base_text, governance, True

    cs = result.get("compliance_scores") or {}
    hr = result.get("hallucination_risks") or {}
    governance = {
        "cs": (min(cs.values()) if cs else None),
        "hr": (max(hr.values()) if hr else None),
        "providers": len(cs),
    }
    prose = _extract_prose(result.get("merged") or "")
    passes = (
        bool(prose)
        and bool(cs)
        and min(cs.values()) >= _CS_FLOOR
        and (not hr or max(hr.values()) <= _HR_CEIL)
    )
    if not passes:
        return base_text, governance, True
    return prose[:_MAX_REPLY_CHARS], governance, False


def _hr_tier(hr):
    if hr is None:
        return None
    if hr <= 0.2:
        return "low"
    if hr <= 0.6:
        return "moderate"
    return "high"


def _persist_trace(bot, call_session_id, intent, caller_text, governance, degraded):
    """Write one DecisionTrace for this turn. Returns the row id or None."""
    try:
        from src.database import SessionLocal
        from src.models import DecisionTrace
        cs01 = (governance.get("cs") / 100.0) if governance.get("cs") is not None else None
        trace = DecisionTrace(
            turn_id=str(uuid.uuid4()),
            source="telephony:%s" % ((bot.name or "bot").strip()[:40]),
            goal_preview=("[%s] %s" % (intent, (caller_text or "").strip()))[:500],
            stages_json=json.dumps({
                "intent": intent,
                "providers": governance.get("providers"),
                "degraded": degraded,
            }),
            avg_cs=cs01,
            avg_hr=governance.get("hr"),
            hr_tier=_hr_tier(governance.get("hr")),
            outcome="degraded" if degraded else "completed",
            aborted=False,
        )
        db = SessionLocal()
        try:
            db.add(trace)
            db.commit()
            return trace.id
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — audit must never break the call
        logger.warning("[TELEPHONY] DecisionTrace persist failed: %s", e)
        return None


def _load_session_and_bot(db, call_session_id):
    from src.models import CallSession, Bot
    cs = db.query(CallSession).filter(CallSession.id == call_session_id).first()
    if not cs:
        return None, None
    bot = db.query(Bot).filter(Bot.id == cs.bot_id).first()
    return cs, bot


async def run_turn(call_session_id, caller_text=None):
    """Run one governed call turn.

    Returns a dict the webhook route turns into TwiML::

        {ok, reply_text, audio_token, content_type, end_call, degraded, trace_id}

    ``audio_token`` is ``None`` when voice synthesis is unavailable; the route
    then falls back to a provider ``<Say>`` of ``reply_text`` (honest degradation,
    the call still proceeds). Raises only on a missing session (programmer error).
    """
    import asyncio

    from src.database import SessionLocal
    db = SessionLocal()
    try:
        cs, bot = _load_session_and_bot(db, call_session_id)
        if not cs or not bot:
            raise ValueError("Unknown call session %s" % call_session_id)
        history = list(cs.transcript or [])
        prior_turns = cs.turns or 0
        intent = "opener" if (prior_turns == 0 and not (caller_text or "").strip()) else "respond"
        # Record the caller's utterance in the transcript first.
        if (caller_text or "").strip():
            history.append({"role": "caller", "text": caller_text.strip(), "ts": _now()})
    finally:
        db.close()

    system, user, base_text = _build_prompts(bot, caller_text, history, intent)
    reply_text, governance, degraded = await _govern_reply(system, user, base_text)

    # Decide whether to end the call: caller said goodbye, or the turn cap is hit.
    end_call = bool(_GOODBYE_RE.search(caller_text or "")) or (prior_turns + 1 >= _MAX_TURNS)

    trace_id = await asyncio.to_thread(
        _persist_trace, bot, call_session_id, intent, caller_text, governance, degraded
    )

    # Synthesize the reply through the broker's governed `voice_synthesize`
    # capability so the TTS step is itself policy-gated and capability-audited
    # (not just the reply text). We pass governed=True + the DecisionTrace id as
    # proof-of-governance — the only governed text that reaches the synthesizer.
    # Best-effort: a missing voice config / provider / governance proof degrades
    # to a provider <Say> of reply_text, it never drops the call.
    audio_token = None
    content_type = "audio/mpeg"
    voice_id = (getattr(bot, "voice_id", None) or "").strip()
    if voice_id:
        try:
            import json as _json

            from src.swarm.broker import CapabilityBroker
            from src.swarm.integrations import Integrations
            from src.swarm.policy import PolicyEngine

            broker = CapabilityBroker(PolicyEngine(), Integrations())
            vres = await broker.request("voice_synthesize", {
                "text": reply_text,
                "voice_id": voice_id,
                "governed": True,
                "decision_trace_id": trace_id,
                "call_session_id": call_session_id,
                "source": "telephony-turn",
            })
            if vres.decision.allowed and vres.output:
                data = _json.loads(vres.output)
                if data.get("ok") and data.get("audio_token"):
                    audio_token = data["audio_token"]
                    content_type = data.get("content_type") or content_type
                else:
                    logger.info("[TELEPHONY] voice synth unavailable, using <Say>: %s",
                                data.get("error"))
            else:
                logger.info("[TELEPHONY] voice synth blocked, using <Say>: %s",
                            vres.decision.reason)
        except Exception as e:  # noqa: BLE001 — fall back to <Say>, never crash the call
            logger.info("[TELEPHONY] voice synth error, using <Say>: %s", e)
            audio_token = None

    # Persist the bot's turn back to the session transcript.
    def _commit_turn():
        db2 = SessionLocal()
        try:
            from src.models import CallSession
            row = db2.query(CallSession).filter(CallSession.id == call_session_id).first()
            if not row:
                return
            hist = list(row.transcript or [])
            if (caller_text or "").strip():
                hist.append({"role": "caller", "text": caller_text.strip(), "ts": _now()})
            hist.append({
                "role": "bot", "text": reply_text, "governance": governance,
                "degraded": degraded, "trace_id": trace_id, "ts": _now(),
            })
            row.transcript = hist
            row.turns = (row.turns or 0) + 1
            if trace_id:
                row.last_decision_trace_id = trace_id
            if row.status in (None, "queued", "ringing"):
                row.status = "in_progress"
                if not row.started_at:
                    row.started_at = datetime.utcnow()
            if end_call:
                row.status = "completed"
                row.ended_at = datetime.utcnow()
            db2.commit()
        finally:
            db2.close()

    await asyncio.to_thread(_commit_turn)

    return {
        "ok": True,
        "reply_text": reply_text,
        "audio_token": audio_token,
        "content_type": content_type,
        "end_call": end_call,
        "degraded": degraded,
        "trace_id": trace_id,
    }
