import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import func, Integer
from src.database import SessionLocal
from src.models import (
    SentinelEvent,
    PerformanceLedgerEntry,
    ProviderMetric,
    ClaimProvenanceRecord,
    SwarmMemoryLayer,
    GovernanceProposal,
    GovernanceVote,
    GovernanceEnactment,
    SwarmRunIndex,
)
from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()
from src.web.templating import templates

EPISTEMIC_LABELS = ("verified", "derived", "hypothesis", "unknown")

# Selectable windows for the epistemic-label trend. "count" windows take the most
# recent N indexed runs; "range" windows take every run within a rolling period.
# Order here drives the order of the selector control in the UI.
EPISTEMIC_WINDOWS = [
    {"key": "10", "label": "Last 10 runs", "mode": "count", "count": 10},
    {"key": "30", "label": "Last 30 runs", "mode": "count", "count": 30},
    {"key": "100", "label": "Last 100 runs", "mode": "count", "count": 100},
    {"key": "24h", "label": "Last 24 hours", "mode": "range", "hours": 24},
    {"key": "7d", "label": "Last 7 days", "mode": "range", "hours": 24 * 7},
    {"key": "30d", "label": "Last 30 days", "mode": "range", "hours": 24 * 30},
]
DEFAULT_EPISTEMIC_WINDOW = "30"


def _resolve_window(window):
    """Return the window spec for a key, falling back to the default if unknown."""
    for w in EPISTEMIC_WINDOWS:
        if w["key"] == window:
            return w
    for w in EPISTEMIC_WINDOWS:
        if w["key"] == DEFAULT_EPISTEMIC_WINDOW:
            return w
    return EPISTEMIC_WINDOWS[0]


def _parse_range_bound(raw, end_of_day=False):
    """Parse an operator-supplied date/datetime bound into a datetime (or None).

    Accepts ``YYYY-MM-DD`` (calendar date) plus the ``YYYY-MM-DDTHH:MM[:SS]``
    forms that ``<input type="date"/"datetime-local">`` emit. A bare date used as
    the upper bound is widened to the end of that day so the range is inclusive.
    Unparseable / empty input returns None (that side of the range is open).
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt
    return None


def _custom_range_label(start, end):
    """Human label for a custom from/to range, tolerating open-ended sides."""
    fmt = "%b %-d, %Y"
    if start and end:
        return f"{start.strftime(fmt)} – {end.strftime(fmt)}"
    if start:
        return f"Since {start.strftime(fmt)}"
    if end:
        return f"Up to {end.strftime(fmt)}"
    return "Custom range"


def _parse_epistemic(raw):
    """Parse an epistemic_summary_json blob into a {label: count} dict."""
    ep = {}
    try:
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            ep = data
    except Exception:
        ep = {}
    out = {}
    for label in EPISTEMIC_LABELS:
        try:
            out[label] = int(ep.get(label, 0) or 0)
        except (TypeError, ValueError):
            out[label] = 0
    return out


def _epistemic_trend(db, window=DEFAULT_EPISTEMIC_WINDOW, date_from=None, date_to=None):
    """Build epistemic-label trend + totals for a selectable run/time window.

    ``window`` is a key from ``EPISTEMIC_WINDOWS`` (e.g. "10"/"30"/"100" runs or
    "24h"/"7d"/"30d" date ranges). Unknown keys fall back to the default. When
    ``date_from`` and/or ``date_to`` are supplied (``YYYY-MM-DD`` or
    ``YYYY-MM-DDTHH:MM``), they take precedence over ``window``: the trend scopes
    to that explicit calendar range (either bound may be left open). Both the
    per-run trend AND the summary totals/ratios are computed over the SAME windowed
    set, so the summary cards recompute when the operator changes the window/range.

    Returns a dict with:
      - points: chronological list of {created_at, total, counts{label:n}, pct{label:n}}
      - totals: sum per label across the windowed runs
      - grand_total: sum of all label counts in the window
      - verified_ratio / unknown_ratio: windowed share of verified vs. unknown
      - run_count: number of runs in the window
      - window / window_label: the resolved window key and human label
      - date_from / date_to: echoed custom-range bounds (empty unless custom)
      - windows: the full list of selectable windows ({key, label})
    """
    start = _parse_range_bound(date_from, end_of_day=False)
    end = _parse_range_bound(date_to, end_of_day=True)
    custom = start is not None or end is not None

    query = db.query(SwarmRunIndex).order_by(SwarmRunIndex.created_at.desc())
    if custom:
        if start is not None:
            query = query.filter(SwarmRunIndex.created_at >= start)
        if end is not None:
            query = query.filter(SwarmRunIndex.created_at <= end)
        recent = query.all()
        window_key = "custom"
        window_label = _custom_range_label(start, end)
    else:
        spec = _resolve_window(window)
        window_key = spec["key"]
        window_label = spec["label"]
        if spec["mode"] == "range":
            cutoff = datetime.utcnow() - timedelta(hours=spec["hours"])
            recent = query.filter(SwarmRunIndex.created_at >= cutoff).all()
        else:
            recent = query.limit(spec["count"]).all()

    points = []
    totals = {label: 0 for label in EPISTEMIC_LABELS}
    for r in reversed(recent):
        counts = _parse_epistemic(r.epistemic_summary_json)
        total = sum(counts.values())
        pct = {
            label: (round(counts[label] / total * 100, 1) if total else 0)
            for label in EPISTEMIC_LABELS
        }
        points.append({
            "run_id": r.run_id,
            "created_at": r.created_at,
            "counts": counts,
            "total": total,
            "pct": pct,
            "aborted": r.aborted,
        })
        for label in EPISTEMIC_LABELS:
            totals[label] += counts[label]

    grand_total = sum(totals.values())
    verified_ratio = round(totals["verified"] / grand_total * 100, 1) if grand_total else 0
    unknown_ratio = round(totals["unknown"] / grand_total * 100, 1) if grand_total else 0

    return {
        "points": points,
        "totals": totals,
        "grand_total": grand_total,
        "verified_ratio": verified_ratio,
        "unknown_ratio": unknown_ratio,
        "run_count": len(points),
        "window": window_key,
        "window_label": window_label,
        "date_from": date_from.strip() if (custom and date_from) else "",
        "date_to": date_to.strip() if (custom and date_to) else "",
        "windows": [{"key": w["key"], "label": w["label"]} for w in EPISTEMIC_WINDOWS],
    }


@router.get("/governance", response_class=HTMLResponse)
async def governance(request: Request, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        alerts = (
            db.query(SentinelEvent)
            .order_by(SentinelEvent.acknowledged.asc(), SentinelEvent.created_at.desc())
            .limit(50)
            .all()
        )
        open_alerts = db.query(SentinelEvent).filter(SentinelEvent.acknowledged == False).count()
        ledger = (
            db.query(PerformanceLedgerEntry)
            .order_by(PerformanceLedgerEntry.created_at.desc())
            .limit(25)
            .all()
        )
        metrics_rows = (
            db.query(
                ProviderMetric.provider,
                func.count(ProviderMetric.id).label("calls"),
                func.avg(ProviderMetric.latency_ms).label("avg_latency"),
                func.sum(func.cast(ProviderMetric.success, Integer)).label("successes"),
                func.avg(ProviderMetric.rating).label("avg_rating"),
            )
            .group_by(ProviderMetric.provider)
            .all()
        )
        metrics = []
        for r in metrics_rows:
            calls = r.calls or 0
            succ = r.successes or 0
            metrics.append({
                "provider": r.provider,
                "calls": calls,
                "avg_latency": round(r.avg_latency or 0),
                "success_rate": round((succ / calls) * 100) if calls else 0,
                "avg_rating": round(r.avg_rating, 2) if r.avg_rating is not None else None,
            })
        total_metrics = db.query(ProviderMetric).count()

        proposals = (
            db.query(GovernanceProposal)
            .order_by(
                GovernanceProposal.status.asc(),
                GovernanceProposal.created_at.desc(),
            )
            .limit(50)
            .all()
        )
        open_proposals = db.query(GovernanceProposal).filter(
            GovernanceProposal.status == "open"
        ).count()

        from src.swarm.governance_engine import GovernanceEngine as _GE
        _ge = _GE()
        proposal_tallies = {}
        for p in proposals:
            try:
                tally = _ge._resolve_vote_tally(db, p.id)
                tally["participation"] = _ge.participation_status(tally, p.track)
                proposal_tallies[p.id] = tally
            except Exception:
                proposal_tallies[p.id] = {
                    "yes": 0.0, "no": 0.0, "abstain": 0.0,
                    "total_effective": 0.0, "participation_weight": 0.0,
                    "yes_ratio": 0.0, "vote_count": 0, "delegation_map": {},
                    "participation": _ge.participation_status({}, p.track),
                }

        enactments = (
            db.query(GovernanceEnactment)
            .order_by(GovernanceEnactment.created_at.desc())
            .limit(20)
            .all()
        )

        epistemic_window = request.query_params.get("window", DEFAULT_EPISTEMIC_WINDOW)
        epistemic_from = request.query_params.get("from", "")
        epistemic_to = request.query_params.get("to", "")
        epistemic_trend = _epistemic_trend(
            db, window=epistemic_window,
            date_from=epistemic_from, date_to=epistemic_to,
        )
    finally:
        db.close()

    cloud_shift_active = True
    exec_locus_dist = []
    try:
        from src.swarm.jurisdiction import get_cloud_shift, provider_exec_locus_distribution
        cloud_shift_active = get_cloud_shift()
        exec_locus_dist = provider_exec_locus_distribution()
    except Exception as e:
        logger.warning("Jurisdiction state lookup failed: %s", e)

    # Structured error counters: surface silent-failure paths so operators can
    # see them instead of having them disappear into the logs.
    failed_indexes = 0
    governance_proposal_failures = 0
    metrics_reset_at = ""
    metrics_reset_by = ""
    try:
        from src.swarm.error_metrics import (
            get_metric, get_last_reset, METRIC_FAILED_INDEXES,
            METRIC_GOVERNANCE_PROPOSAL_FAILURES,
        )
        failed_indexes = get_metric(METRIC_FAILED_INDEXES)
        governance_proposal_failures = get_metric(METRIC_GOVERNANCE_PROPOSAL_FAILURES)
        metrics_reset_at, metrics_reset_by = get_last_reset()
    except Exception as e:
        logger.warning("Error-metric lookup failed: %s", e)

    return templates.TemplateResponse("governance.html", {
        "request": request,
        "alerts": alerts,
        "open_alerts": open_alerts,
        "ledger": ledger,
        "metrics": metrics,
        "total_metrics": total_metrics,
        "cloud_shift_active": cloud_shift_active,
        "exec_locus_dist": exec_locus_dist,
        "failed_indexes": failed_indexes,
        "governance_proposal_failures": governance_proposal_failures,
        "metrics_reset_at": metrics_reset_at,
        "metrics_reset_by": metrics_reset_by,
        "proposals": proposals,
        "open_proposals": open_proposals,
        "proposal_tallies": proposal_tallies,
        "enactments": enactments,
        "epistemic_trend": epistemic_trend,
        "epistemic_labels": EPISTEMIC_LABELS,
        "flash": request.query_params.get("flash"),
    })


@router.post("/governance/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        ev = db.query(SentinelEvent).filter(SentinelEvent.id == alert_id).first()
        if ev:
            ev.acknowledged = True
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    return RedirectResponse(url="/governance?flash=Alert acknowledged", status_code=303)


@router.post("/governance/metrics/reset")
async def reset_swarm_metrics(metric: str = Form("all"), _=Depends(require_auth)):
    from urllib.parse import quote
    try:
        from src.swarm.error_metrics import RESETTABLE_METRICS, reset_metric, reset_metrics
        metric = (metric or "all").strip()
        if metric == "all":
            ok = reset_metrics(operator="operator")
            msg = "Swarm-health counters reset." if ok else "Reset failed: storage error."
        elif metric in RESETTABLE_METRICS:
            ok = reset_metric(metric, operator="operator")
            msg = "Counter reset." if ok else "Reset failed: storage error."
        else:
            msg = "Reset failed: unknown counter."
    except Exception as e:
        logger.warning("Metric reset failed: %s", e)
        msg = "Reset failed: internal error."
    return RedirectResponse(url="/governance?flash=" + quote(msg), status_code=303)


@router.post("/governance/proposals/{proposal_id}/vote")
async def vote_proposal(
    proposal_id: int,
    vote: str = Form(""),
    reason: str = Form(""),
    _=Depends(require_auth),
):
    from urllib.parse import quote
    vote = vote.strip().lower()
    if vote not in ("yes", "no", "abstain"):
        return RedirectResponse(
            url="/governance?flash=" + quote("Invalid vote value."),
            status_code=303,
        )
    try:
        from src.swarm.governance_engine import GovernanceEngine
        ok = GovernanceEngine().cast_vote(
            proposal_db_id=proposal_id,
            voter="operator",
            vote=vote,
            weight=1.0,
            reason=reason.strip()[:500],
        )
        msg = "Vote recorded." if ok else "Vote failed: proposal may be closed or not found."
    except Exception as e:
        logger.warning("Vote failed: %s", e)
        msg = "Vote failed: internal error."
    return RedirectResponse(url="/governance?flash=" + quote(msg), status_code=303)


@router.post("/governance/proposals/{proposal_id}/enact")
async def enact_proposal(
    proposal_id: int,
    _=Depends(require_auth),
):
    from urllib.parse import quote
    try:
        from src.swarm.governance_engine import GovernanceEngine
        ok = GovernanceEngine().operator_enact(proposal_db_id=proposal_id, operator="admin")
        msg = "Proposal enacted." if ok else "Enactment failed: proposal may be closed or not found."
    except Exception as e:
        logger.warning("Enact failed: %s", e)
        msg = "Enactment failed: internal error."
    return RedirectResponse(url="/governance?flash=" + quote(msg), status_code=303)


@router.post("/governance/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    _=Depends(require_auth),
):
    from urllib.parse import quote
    try:
        from src.swarm.governance_engine import GovernanceEngine
        ok = GovernanceEngine().operator_reject(proposal_db_id=proposal_id)
        msg = "Proposal rejected." if ok else "Rejection failed: proposal may be closed or not found."
    except Exception as e:
        logger.warning("Reject failed: %s", e)
        msg = "Rejection failed: internal error."
    return RedirectResponse(url="/governance?flash=" + quote(msg), status_code=303)


@router.post("/governance/proposals/{proposal_id}/delegate")
async def delegate_proposal(
    proposal_id: int,
    delegate_to: str = Form(""),
    _=Depends(require_auth),
):
    from urllib.parse import quote
    delegate_to = delegate_to.strip()[:100]
    if not delegate_to:
        return RedirectResponse(
            url="/governance?flash=" + quote("Delegate-to voter name is required."),
            status_code=303,
        )
    try:
        from src.swarm.governance_engine import GovernanceEngine
        ok = GovernanceEngine().delegate(
            proposal_db_id=proposal_id,
            delegator="operator",
            delegate_to=delegate_to,
        )
        msg = f"Vote delegated to {delegate_to}." if ok else "Delegation failed."
    except Exception as e:
        logger.warning("Delegate failed: %s", e)
        msg = "Delegation failed: internal error."
    return RedirectResponse(url="/governance?flash=" + quote(msg), status_code=303)


@router.get("/runs/search", response_class=HTMLResponse)
async def runs_search(request: Request, _=Depends(require_auth)):
    q = (request.query_params.get("q") or "").strip()
    aborted_filter = request.query_params.get("aborted", "").strip().lower()
    try:
        page = max(1, int(request.query_params.get("page", 1) or 1))
    except (TypeError, ValueError):
        page = 1
    per_page = 25
    db = SessionLocal()
    try:
        query = db.query(SwarmRunIndex)
        if q:
            like = f"%{q}%"
            query = query.filter(
                SwarmRunIndex.goal.ilike(like)
                | SwarmRunIndex.synthesis.ilike(like)
                | SwarmRunIndex.epistemic_summary_json.ilike(like)
                | SwarmRunIndex.provenance_lineage_json.ilike(like)
                | SwarmRunIndex.claim_text.ilike(like)
                | SwarmRunIndex.dissent_json.ilike(like)
            )
        if aborted_filter == "yes":
            query = query.filter(SwarmRunIndex.aborted == True)
        elif aborted_filter == "no":
            query = query.filter(SwarmRunIndex.aborted == False)
        total = query.count()
        runs = (
            query.order_by(SwarmRunIndex.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        runs_data = []
        for r in runs:
            ep = _parse_epistemic(r.epistemic_summary_json)
            ep_total = sum(ep.values())
            ep_pct = {
                label: (round(ep[label] / ep_total * 100, 1) if ep_total else 0)
                for label in EPISTEMIC_LABELS
            }
            runs_data.append({
                "id": r.id,
                "run_id": r.run_id,
                "goal": r.goal,
                "domain": r.domain or "general",
                "synthesis": r.synthesis or "",
                "epistemic": ep,
                "epistemic_total": ep_total,
                "epistemic_pct": ep_pct,
                "dissent_count": r.dissent_count or 0,
                "aborted": r.aborted,
                "avg_cs": r.avg_cs,
                "avg_hr": r.avg_hr,
                "meta_claims_count": r.meta_claims_count or 0,
                "task_run_id": r.task_run_id,
                "created_at": r.created_at,
            })
    finally:
        db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("runs_search.html", {
        "request": request,
        "runs": runs_data,
        "q": q,
        "aborted_filter": aborted_filter,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/provenance", response_class=HTMLResponse)
async def provenance(request: Request, _=Depends(require_auth)):
    q = (request.query_params.get("q") or "").strip()
    try:
        page = max(1, int(request.query_params.get("page", 1) or 1))
    except (TypeError, ValueError):
        page = 1
    per_page = 30
    db = SessionLocal()
    try:
        query = db.query(ClaimProvenanceRecord)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (ClaimProvenanceRecord.claim_text.ilike(like))
                | (ClaimProvenanceRecord.origin_role.ilike(like))
                | (ClaimProvenanceRecord.origin_provider.ilike(like))
            )
        total = query.count()
        records = (
            query.order_by(ClaimProvenanceRecord.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
    finally:
        db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("provenance.html", {
        "request": request,
        "records": records,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })



@router.get("/system", response_class=HTMLResponse)
async def system_health(request: Request, _=Depends(require_auth)):
    results = {}
    error = None
    try:
        from src.selfheal.circuit_breaker import CircuitBreaker
        from src.selfheal.health_monitor import HealthMonitor

        cb = CircuitBreaker()
        monitor = HealthMonitor(circuit_breaker=cb)
        results = await monitor.run_checks()
    except Exception as e:
        logger.error("Health check failed: %s", e)
        error = str(e)
    return templates.TemplateResponse("system.html", {
        "request": request,
        "results": results,
        "error": error,
    })
