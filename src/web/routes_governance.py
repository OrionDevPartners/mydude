import json
import logging
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
                proposal_tallies[p.id] = tally
            except Exception:
                proposal_tallies[p.id] = {
                    "yes": 0.0, "no": 0.0, "abstain": 0.0,
                    "total_effective": 0.0, "yes_ratio": 0.0,
                    "vote_count": 0, "delegation_map": {},
                }

        enactments = (
            db.query(GovernanceEnactment)
            .order_by(GovernanceEnactment.created_at.desc())
            .limit(20)
            .all()
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

    return templates.TemplateResponse("governance.html", {
        "request": request,
        "alerts": alerts,
        "open_alerts": open_alerts,
        "ledger": ledger,
        "metrics": metrics,
        "total_metrics": total_metrics,
        "cloud_shift_active": cloud_shift_active,
        "exec_locus_dist": exec_locus_dist,
        "proposals": proposals,
        "open_proposals": open_proposals,
        "proposal_tallies": proposal_tallies,
        "enactments": enactments,
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
            ep = {}
            try:
                ep = json.loads(r.epistemic_summary_json or "{}")
            except Exception:
                pass
            runs_data.append({
                "id": r.id,
                "run_id": r.run_id,
                "goal": r.goal,
                "domain": r.domain or "general",
                "synthesis": r.synthesis or "",
                "epistemic": ep,
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


@router.get("/memory", response_class=HTMLResponse)
async def memory(request: Request, _=Depends(require_auth)):
    q = (request.query_params.get("q") or "").strip()
    layer = (request.query_params.get("layer") or "").strip()
    db = SessionLocal()
    try:
        query = db.query(SwarmMemoryLayer)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (SwarmMemoryLayer.content.ilike(like))
                | (SwarmMemoryLayer.summary.ilike(like))
                | (SwarmMemoryLayer.topic.ilike(like))
            )
        if layer:
            query = query.filter(SwarmMemoryLayer.layer_type == layer)
        layers = (
            query.order_by(SwarmMemoryLayer.created_at.desc())
            .limit(100)
            .all()
        )
        layer_types = [
            r[0] for r in db.query(SwarmMemoryLayer.layer_type)
            .distinct()
            .all()
            if r[0]
        ]
        total = db.query(SwarmMemoryLayer).count()
    finally:
        db.close()
    return templates.TemplateResponse("memory.html", {
        "request": request,
        "layers": layers,
        "layer_types": layer_types,
        "q": q,
        "layer": layer,
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
