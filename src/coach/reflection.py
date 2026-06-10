"""Periodic reflection: surface longitudinal patterns as cited CoachInsights.

Reads recent MoodSignal rows (Postgres = system of record), enumerates them
[S1]..[Sn], and asks the governed swarm to identify real longitudinal patterns
(e.g. rising financial stress alongside low mood, or meeting-density spikes
preceding negative valence). Each surfaced insight cites the signals it rests on
and proposes one concrete micro-action.

Governance:
  - Fail loud: too few signals -> ``insufficient_data`` (we never invent a pattern).
  - Citations are mandatory; an insight with no resolvable citations is dropped.
  - Insights are also written back as LOCAL-ONLY memory nodes; outcomes logged via
    ``log_outcome`` close the loop (also local-only).
  - Honors ``COACH_STRICT_PRIVATE`` (pins inference to local providers).
"""
import json
import logging
import re
from datetime import datetime, timedelta

from src.models import MoodSignal, CoachInsight, CoachAuditLog
from src.coach.llm import call_team_sync

logger = logging.getLogger(__name__)

_VALID_SEVERITY = ("info", "watch", "elevated", "high")
_ALLOWED_OUTCOME = ("acknowledged", "actioned", "dismissed")

_REFLECT_SYS = (
    "You are a careful longitudinal pattern analyst for a life-coaching platform. "
    "You are given time-stamped mood/behavior signals. Identify ONLY genuine, "
    "well-supported patterns across them (trends, correlations, risks such as "
    "burnout). Do NOT invent patterns that the signals do not support; returning "
    "an empty list is correct when nothing stands out. Never diagnose medically.\n\n"
    "Return ONLY a JSON array (no prose, no code fences). Each element: "
    '{"kind": "pattern|risk", "title": <short>, "detail": <2-3 sentences>, '
    '"severity": "info|watch|elevated|high", "micro_action": <one concrete small '
    'step>, "confidence": <0..1>, "citations": ["S1","S3", ...]}. '
    "Every element MUST cite at least one signal by its [S#] ref."
)

_REFLECT_USER = (
    "Signals (most recent window):\n%s\n\n"
    "Return the JSON array of supported patterns now."
)


def _audit(db, action, status, detail):
    db.add(CoachAuditLog(action=action, status=status, source="coach-reflect",
                         detail=detail))
    db.commit()


def _norm_severity(v):
    s = (str(v or "").strip().lower())
    return s if s in _VALID_SEVERITY else "info"


def _clampf(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _parse_insights(text):
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _serialize_insight(row):
    cites = None
    if row.citations_json:
        try:
            cites = json.loads(row.citations_json)
        except (json.JSONDecodeError, ValueError):
            cites = None
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "detail": row.detail,
        "severity": row.severity,
        "micro_action": row.micro_action,
        "citations": cites,
        "confidence": row.confidence,
        "status": row.status,
        "outcome": row.outcome,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _write_insight_memory(row):
    try:
        from src.memory.substrate import get_substrate
        get_substrate().write_claim(
            content="Coach insight [%s/%s]: %s — %s"
                    % (row.kind, row.severity, row.title, (row.detail or "")[:200]),
            category="coach",
            confidence=float(row.confidence or 0.5),
            source="coach:reflection",
            verified=False,
            metadata={"insight_id": row.id, "severity": row.severity},
            local_only=True,
        )
    except Exception as e:
        logger.warning("Insight memory node write skipped: %s", e)


def run_reflection(db, lookback_days=30, max_signals=60, min_signals=4,
                   strict_private=None):
    """Surface cited insights from the recent signal window. Fail loud on too few."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(MoodSignal)
        .filter(MoodSignal.observed_at >= cutoff)
        .order_by(MoodSignal.observed_at.asc())
        .limit(max_signals)
        .all()
    )
    if len(rows) < min_signals:
        _audit(db, "reflect", "insufficient_data",
               "Only %d signals in %dd window." % (len(rows), lookback_days))
        return {
            "status": "insufficient_data",
            "insights": [],
            "message": "Not enough signals (%d) to surface reliable patterns yet."
                       % len(rows),
        }

    sref = {}
    lines = []
    for idx, s in enumerate(rows, 1):
        ref = "S%d" % idx
        sref[ref] = s
        when = s.observed_at.strftime("%Y-%m-%d %H:%M") if s.observed_at else "?"
        lines.append(
            "[%s] %s | %s/%s | valence=%s score=%s | %s" % (
                ref, when, s.signal_type, s.label or "-",
                ("%.2f" % s.valence) if s.valence is not None else "na",
                ("%.2f" % s.score) if s.score is not None else "na",
                (s.summary or "")[:120],
            )
        )

    if strict_private is None:
        from src.web.settings_store import get_setting
        strict_private = (get_setting("COACH_STRICT_PRIVATE", "0") or "0") == "1"

    result = call_team_sync(_REFLECT_SYS, _REFLECT_USER % "\n".join(lines),
                            strict_private=strict_private)
    parsed = _parse_insights(result.get("merged") or "")

    created = []
    for ins in parsed:
        if not isinstance(ins, dict):
            continue
        refs = [c for c in (ins.get("citations") or []) if c in sref]
        if not refs:
            continue  # drop uncited insights — grounding is mandatory
        citation_payload = [
            {"ref": r, "signal_id": sref[r].id, "memory_id": sref[r].memory_id}
            for r in refs
        ]
        row = CoachInsight(
            kind=(str(ins.get("kind") or "pattern"))[:40],
            title=(str(ins.get("title") or "Pattern"))[:200],
            detail=ins.get("detail"),
            severity=_norm_severity(ins.get("severity")),
            micro_action=ins.get("micro_action"),
            citations_json=json.dumps(citation_payload),
            confidence=_clampf(ins.get("confidence"), 0.0, 1.0, 0.5),
            status="open",
            source="reflection",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _write_insight_memory(row)
        created.append(_serialize_insight(row))

    _audit(db, "reflect", "ok",
           "Surfaced %d insight(s) from %d signals." % (len(created), len(rows)))
    return {"status": "ok", "insights": created}


def log_outcome(db, insight_id, status, outcome=None):
    """Record the operator's outcome on an insight (closes the coaching loop)."""
    if status not in _ALLOWED_OUTCOME:
        raise ValueError("Unsupported outcome '%s'. Allowed: %s"
                         % (status, ", ".join(_ALLOWED_OUTCOME)))
    row = db.query(CoachInsight).filter(CoachInsight.id == insight_id).first()
    if row is None:
        raise ValueError("Insight %s not found." % insight_id)
    row.status = status
    if outcome:
        row.outcome = outcome
    db.commit()
    db.refresh(row)

    try:
        from src.memory.substrate import get_substrate
        get_substrate().write_claim(
            content="Coach insight '%s' outcome: %s. %s"
                    % (row.title, status, outcome or ""),
            category="coach",
            confidence=0.8,
            source="coach:outcome",
            verified=True,
            metadata={"insight_id": row.id, "status": status},
            local_only=True,
        )
    except Exception as e:
        logger.warning("Outcome memory node write skipped: %s", e)

    _audit(db, "insight_outcome", "ok", "Insight #%d -> %s." % (row.id, status))
    return _serialize_insight(row)


def list_insights(db, limit=50, status=None):
    q = db.query(CoachInsight)
    if status:
        q = q.filter(CoachInsight.status == status)
    rows = q.order_by(CoachInsight.id.desc()).limit(int(limit)).all()
    return [_serialize_insight(r) for r in rows]
