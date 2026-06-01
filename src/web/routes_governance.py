import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, Integer
from src.database import SessionLocal
from src.models import (
    SentinelEvent,
    PerformanceLedgerEntry,
    ProviderMetric,
    ClaimProvenanceRecord,
    SwarmMemoryLayer,
)
from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


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
    finally:
        db.close()
    return templates.TemplateResponse("governance.html", {
        "request": request,
        "alerts": alerts,
        "open_alerts": open_alerts,
        "ledger": ledger,
        "metrics": metrics,
        "total_metrics": total_metrics,
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
