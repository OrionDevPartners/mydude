"""Normalize emotion / sentiment / behavior signals into MoodSignal rows + a
LOCAL-ONLY memory node (the longitudinal 'digital twin').

Postgres is the system of record (purgeable, holds the full provider payload). The
memory node is written with ``local_only=True`` so the sensitive content lives in
the local knowledge graph but NEVER egresses via the cloud adapter (Private-Mode).
The node text is a short, neutral, time-stamped summary — never a raw private
journal entry verbatim beyond what the operator submitted.
"""
import json
import logging
from datetime import datetime

from src.models import MoodSignal, CoachAuditLog

logger = logging.getLogger(__name__)


def _audit(db, action, status, detail):
    db.add(CoachAuditLog(action=action, status=status, source="coach-ingest",
                         detail=detail))
    db.commit()


def _serialize(sig):
    metrics = None
    if sig.metrics_json:
        try:
            metrics = json.loads(sig.metrics_json)
        except (json.JSONDecodeError, ValueError):
            metrics = None
    return {
        "id": sig.id,
        "signal_type": sig.signal_type,
        "source": sig.source,
        "observed_at": sig.observed_at.isoformat() if sig.observed_at else None,
        "valence": sig.valence,
        "arousal": sig.arousal,
        "score": sig.score,
        "label": sig.label,
        "summary": sig.summary,
        "metrics": metrics,
        "project_id": sig.project_id,
        "event_ref": sig.event_ref,
        "memory_id": sig.memory_id,
        "private": sig.private,
        "created_at": sig.created_at.isoformat() if sig.created_at else None,
    }


def _node_summary(signal_type, source, label, valence, event_ref, observed):
    parts = ["%s signal (%s)" % (signal_type, source)]
    if label:
        parts.append("dominant=%s" % label)
    if valence is not None:
        parts.append("valence=%.2f" % valence)
    base = ", ".join(parts)
    if event_ref:
        base += " [context: %s]" % event_ref
    return "On %s, %s." % (observed.strftime("%Y-%m-%d %H:%M"), base)


def _write_signal(db, *, signal_type, source, observed_at=None, valence=None,
                  arousal=None, score=None, label=None, summary=None,
                  metrics=None, project_id=None, event_ref=None,
                  category="mood"):
    """Create a MoodSignal row + a LOCAL-ONLY memory node. Returns the ORM row."""
    observed = observed_at or datetime.utcnow()
    node_text = _node_summary(signal_type, source, label, valence, event_ref, observed)

    memory_id = None
    try:
        from src.memory.substrate import get_substrate
        entry = get_substrate().write_claim(
            content=node_text,
            category=category,
            confidence=0.7,
            source="coach:%s" % source,
            verified=False,
            metadata={
                "signal_type": signal_type,
                "label": label,
                "valence": valence,
                "project_id": project_id,
                "event_ref": event_ref,
            },
            local_only=True,  # Private-Mode: emotional/personal data never egresses
        )
        memory_id = entry.memory_id
    except Exception as e:
        logger.warning("Mood memory node write skipped: %s", e)

    sig = MoodSignal(
        signal_type=signal_type,
        source=source,
        observed_at=observed,
        valence=valence,
        arousal=arousal,
        score=score,
        label=label,
        summary=summary or node_text,
        metrics_json=json.dumps(metrics) if metrics else None,
        project_id=project_id,
        event_ref=event_ref,
        memory_id=memory_id,
        private=True,
    )
    db.add(sig)
    db.commit()
    db.refresh(sig)
    return sig


def ingest_text(db, text, prefer="auto", observed_at=None, project_id=None,
                event_ref=None, strict_private=None):
    """Capture a mood signal from free text.

    ``prefer``: 'emotion' (Hume, fail loud if unconfigured), 'sentiment'
    (governed LLM), or 'auto' (Hume if connected, else governed LLM).

    ``strict_private`` (read from ``COACH_STRICT_PRIVATE`` when ``None``) keeps
    raw text on-device: it pins sentiment inference to LOCAL LLMs and REFUSES the
    Hume cloud emotion path (fail loud) rather than silently egressing the text.
    """
    if not text or not text.strip():
        raise ValueError("Cannot ingest empty text.")

    if strict_private is None:
        from src.web.settings_store import get_setting
        strict_private = (get_setting("COACH_STRICT_PRIVATE", "0") or "0") == "1"

    mode = (prefer or "auto").lower()
    use_emotion = mode == "emotion"
    if mode == "auto":
        # In strict-private mode never route raw text to the Hume cloud provider;
        # fall back to local-pinned sentiment instead.
        if strict_private:
            use_emotion = False
        else:
            try:
                from src.coach.providers import hume_status
                use_emotion = bool(hume_status().get("connected"))
            except Exception:
                use_emotion = False

    if use_emotion:
        if strict_private:
            raise ValueError(
                "Strict-private mode is on: emotion analysis uses the Hume cloud "
                "provider, which would send your text off-device. Use sentiment "
                "analysis (local) or disable strict-private mode."
            )
        from src.coach.providers import get_mood_provider
        provider = get_mood_provider()  # raises CoachNotConfigured if unavailable
        res = provider.analyze_text(text)
        sig = _write_signal(
            db, signal_type="emotion", source=res.get("provider", "hume"),
            observed_at=observed_at, valence=res.get("valence"),
            arousal=res.get("arousal"), score=res.get("score"),
            label=res.get("label"), metrics=res, project_id=project_id,
            event_ref=event_ref, category="mood",
        )
        _audit(db, "ingest_emotion", "ok",
               "Emotion signal #%d via %s." % (sig.id, res.get("provider")))
        return _serialize(sig)

    from src.coach.sentiment import analyze_text_sentiment
    res = analyze_text_sentiment(text, strict_private=strict_private)
    sig = _write_signal(
        db, signal_type="sentiment", source="llm_sentiment",
        observed_at=observed_at, valence=res.get("valence"),
        arousal=res.get("arousal"), label=res.get("label"),
        summary=res.get("summary"), metrics=res, project_id=project_id,
        event_ref=event_ref, category="mood",
    )
    _audit(db, "ingest_sentiment", "ok",
           "Sentiment signal #%d via governed LLM." % sig.id)
    return _serialize(sig)


def write_behavior_signal(db, *, source, score=None, valence=None, label=None,
                          summary=None, metrics=None, project_id=None,
                          event_ref=None, observed_at=None):
    """Persist a derived behavioral signal (calendar density, financial stress)."""
    sig = _write_signal(
        db, signal_type="behavior", source=source, observed_at=observed_at,
        valence=valence, score=score, label=label, summary=summary,
        metrics=metrics, project_id=project_id, event_ref=event_ref,
        category="behavior",
    )
    _audit(db, "ingest_behavior", "ok",
           "Behavior signal #%d (%s)." % (sig.id, source))
    return _serialize(sig)


def recent_signals(db, limit=50, signal_type=None):
    """Most-recent mood/behavior signals (newest first)."""
    q = db.query(MoodSignal)
    if signal_type:
        q = q.filter(MoodSignal.signal_type == signal_type)
    rows = q.order_by(MoodSignal.id.desc()).limit(int(limit)).all()
    return [_serialize(r) for r in rows]


def purge_signals(db, ids=None):
    """Private-Mode delete (right to be forgotten).

    Deletes MoodSignal rows (all, or a specific list of ids) AND forgets their
    linked LOCAL-ONLY memory nodes from both memory stores. Returns counts so the
    caller can report exactly what was removed — never a silent no-op.
    """
    q = db.query(MoodSignal)
    if ids:
        q = q.filter(MoodSignal.id.in_([int(i) for i in ids]))
    rows = q.all()
    memory_ids = [r.memory_id for r in rows if r.memory_id]

    forgotten = 0
    if memory_ids:
        try:
            from src.memory.substrate import get_substrate
            forgotten = get_substrate().forget(memory_ids)
        except Exception as e:
            logger.warning("Purge memory forget failed: %s", e)

    deleted = 0
    for r in rows:
        db.delete(r)
        deleted += 1
    db.commit()

    _audit(db, "purge_signals", "ok",
           "Purged %d signal row(s); forgot %d memory node(s)." % (deleted, forgotten))
    return {"deleted_signals": deleted, "forgotten_memories": forgotten}
