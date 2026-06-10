"""Derive behavioral signals from existing app data — no new external provider for
financial stress, and a real (connector-gated) read for calendar density.

Two signals:
  - financial_stress: computed from the finance sub-stack (budget vs actuals +
    unattributed spend). Returns None when there is no finance data — it never
    fabricates a zero-stress reading.
  - calendar_density: read live from the Google Calendar connector (or vault
    token). Returns None when no calendar provider is connected; raises (caught
    by ``compute_signals`` and reported as skipped) on an auth/API error.

Both honor governance pillar #1: no placeholder/fabricated signals. Available
signals are persisted as MoodSignal(signal_type='behavior') rows with local-only
memory nodes.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.models import FinanceTransaction
from src.coach.providers import CoachAuthError, CoachProviderError

logger = logging.getLogger(__name__)

_CAL_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_CAL_TIMEOUT = 20.0


def _clamp01(v):
    return max(0.0, min(1.0, float(v)))


# --------------------------------------------------------------------------- #
# Financial stress (from the finance sub-stack)
# --------------------------------------------------------------------------- #

def financial_stress(db):
    """Return a behavior-signal dict or None when there is no finance data."""
    total_txns = db.query(FinanceTransaction).count()
    if total_txns == 0:
        return None

    from src.finance.budget import budget_vs_actuals
    report = budget_vs_actuals(db)
    projects = report.get("projects") or []
    unattr = report.get("unattributed") or {}

    attributed_total = sum(float(p.get("actual_total") or 0.0) for p in projects)
    unattr_total = float(unattr.get("total") or 0.0)
    spend_total = attributed_total + unattr_total

    over_budget = sum(1 for p in projects if "over_budget" in (p.get("flags") or []))
    near_limit = sum(1 for p in projects if "near_limit" in (p.get("flags") or []))
    unattr_ratio = (unattr_total / spend_total) if spend_total > 0 else 0.0

    score = _clamp01(
        0.45 * unattr_ratio
        + 0.40 * min(1.0, over_budget / 2.0)
        + 0.15 * min(1.0, near_limit / 3.0)
    )
    # More financial stress reads as lower (more negative) valence.
    valence = round(-score, 4)

    drivers = []
    if over_budget:
        drivers.append("%d project(s) over budget" % over_budget)
    if near_limit:
        drivers.append("%d near budget limit" % near_limit)
    if unattr_ratio > 0:
        drivers.append("%.0f%% of spend unattributed" % (unattr_ratio * 100))
    summary = "Financial stress %.2f (%s)." % (
        score, "; ".join(drivers) if drivers else "spend within budgets")

    return {
        "score": round(score, 4),
        "valence": valence,
        "label": "financial_stress",
        "summary": summary,
        "metrics": {
            "over_budget": over_budget,
            "near_limit": near_limit,
            "unattributed_ratio": round(unattr_ratio, 4),
            "spend_total": round(spend_total, 2),
            "transactions": total_txns,
        },
    }


# --------------------------------------------------------------------------- #
# Calendar density (Google Calendar connector / vault token)
# --------------------------------------------------------------------------- #

def _calendar_token():
    try:
        from src.web.connectors import get_access_token
        token = get_access_token("google-calendar") or get_access_token("googlecalendar")
    except Exception:
        token = None
    if not token:
        from src.providers.secrets import get_secret
        token = get_secret("GOOGLE_CALENDAR_ACCESS_TOKEN")
    return token


def _parse_rfc3339(s):
    s = (s or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _calendar_events(window_days):
    token = _calendar_token()
    if not token:
        return None
    now = datetime.now(timezone.utc)
    params = {
        "timeMin": now.isoformat(),
        "timeMax": (now + timedelta(days=window_days)).isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 250,
    }
    headers = {"Authorization": "Bearer %s" % token, "Accept": "application/json"}
    try:
        resp = httpx.get(_CAL_URL, params=params, headers=headers, timeout=_CAL_TIMEOUT)
    except httpx.HTTPError as e:
        raise CoachProviderError("Calendar request failed: %s" % e)
    if resp.status_code in (401, 403):
        raise CoachAuthError(
            "Calendar rejected the request (HTTP %d). Reconnect Google Calendar."
            % resp.status_code
        )
    if resp.status_code >= 400:
        raise CoachProviderError(
            "Calendar API error (HTTP %d): %s" % (resp.status_code, resp.text[:200])
        )
    return resp.json().get("items", []) or []


def calendar_density(db, window_days=7):
    """Return a behavior-signal dict, or None when no calendar provider connected."""
    events = _calendar_events(window_days)
    if events is None:
        return None

    count = len(events)
    total_hours = 0.0
    for e in events:
        start = (e.get("start") or {}).get("dateTime")
        end = (e.get("end") or {}).get("dateTime")
        if not start or not end:
            continue  # all-day event — no measurable duration
        try:
            total_hours += max(0.0, (_parse_rfc3339(end) - _parse_rfc3339(start))
                               .total_seconds() / 3600.0)
        except (ValueError, TypeError):
            pass

    per_day = count / float(window_days)
    # Saturate at ~5 meetings/day or ~6 meeting-hours/day.
    score = _clamp01(max(per_day / 5.0, (total_hours / window_days) / 6.0))
    summary = "%d events over %d days (%.1f/day, %.1f meeting-hours)." % (
        count, window_days, per_day, total_hours)

    return {
        "score": round(score, 4),
        "valence": None,
        "label": "calendar_density",
        "summary": summary,
        "metrics": {
            "events": count,
            "window_days": window_days,
            "per_day": round(per_day, 2),
            "meeting_hours": round(total_hours, 2),
        },
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def compute_signals(db, persist=True, window_days=7):
    """Derive all available behavioral signals.

    Persists each available signal (when ``persist``) and reports which signals
    were skipped and why — so the dashboard is honest about unavailable inputs.
    """
    out = {"written": [], "skipped": []}
    derivers = (
        ("financial_stress", lambda: financial_stress(db)),
        ("calendar_density", lambda: calendar_density(db, window_days)),
    )
    for name, fn in derivers:
        try:
            sig = fn()
        except Exception as e:
            out["skipped"].append({"signal": name, "reason": str(e)})
            continue
        if not sig:
            out["skipped"].append(
                {"signal": name, "reason": "no data / provider not connected"})
            continue
        if persist:
            from src.coach.ingestion import write_behavior_signal
            out["written"].append(write_behavior_signal(
                db, source=name, score=sig.get("score"), valence=sig.get("valence"),
                label=sig.get("label"), summary=sig.get("summary"),
                metrics=sig.get("metrics"),
            ))
        else:
            out["written"].append(sig)
    return out
