"""
Memory routes — dashboard-visible memory status, manual sync trigger,
SwarmMemoryLayer search, and audit log for the recursive memory substrate.
"""

import json
import logging
import time
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from src.database import SessionLocal
from src.models import SwarmMemoryLayer
from src.web.auth import require_auth
from src.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_substrate():
    try:
        from src.memory import get_substrate
        return get_substrate()
    except Exception as e:
        logger.warning("Memory substrate unavailable: %s", e)
        return None


@router.get("/memory", response_class=HTMLResponse)
async def memory_status(request: Request, _=Depends(require_auth)):
    substrate = _get_substrate()
    status = {}
    events = []
    if substrate is not None:
        try:
            status = substrate.status()
            events = substrate.audit_events(limit=50)
        except Exception as e:
            logger.warning("Memory status fetch failed: %s", e)

    # Also load SwarmMemoryLayer entries for the legacy list view
    q = (request.query_params.get("q") or "").strip()
    layer_filter = (request.query_params.get("layer") or "").strip()
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
        if layer_filter:
            query = query.filter(SwarmMemoryLayer.layer_type == layer_filter)
        layers = query.order_by(SwarmMemoryLayer.created_at.desc()).limit(100).all()
        layer_types = [
            r[0] for r in db.query(SwarmMemoryLayer.layer_type).distinct().all()
            if r[0]
        ]
        total_layers = db.query(SwarmMemoryLayer).count()
    except Exception as e:
        logger.warning("SwarmMemoryLayer query failed: %s", e)
        layers = []
        layer_types = []
        total_layers = 0
    finally:
        db.close()

    return templates.TemplateResponse("memory.html", {
        "request": request,
        "status": status,
        "events": events,
        "layers": layers,
        "layer_types": layer_types,
        "q": q,
        "layer": layer_filter,
        "total": total_layers,
        "err": request.query_params.get("err"),
        "msg": request.query_params.get("msg"),
    })


@router.post("/memory/sync")
async def memory_sync(request: Request, _=Depends(require_auth)):
    substrate = _get_substrate()
    if substrate is None:
        return RedirectResponse(
            url="/memory?err=Memory+substrate+unavailable",
            status_code=303,
        )
    try:
        form = await request.form()
        direction = str(form.get("direction", "both"))
        if direction not in ("both", "local→cloud", "cloud→local"):
            direction = "both"
        report = substrate.sync(direction=direction, min_confidence=0.5)  # type: ignore[arg-type]
        msg = report.summary().replace(" ", "+")
        return RedirectResponse(url=f"/memory?msg={msg}", status_code=303)
    except Exception as e:
        logger.error("Memory sync failed: %s", e)
        return RedirectResponse(
            url="/memory?err=Sync+failed:+" + str(e)[:60].replace(" ", "+"),
            status_code=303,
        )


@router.post("/memory/consolidate")
async def memory_consolidate(request: Request, _=Depends(require_auth)):
    substrate = _get_substrate()
    if substrate is None:
        return RedirectResponse(
            url="/memory?err=Memory+substrate+unavailable",
            status_code=303,
        )
    try:
        promoted = substrate.consolidate(min_confidence=0.75)
        return RedirectResponse(
            url=f"/memory?msg=Consolidated:+promoted+{promoted}+entries",
            status_code=303,
        )
    except Exception as e:
        logger.error("Memory consolidation failed: %s", e)
        return RedirectResponse(
            url="/memory?err=Consolidation+failed",
            status_code=303,
        )


@router.get("/memory/api/status")
async def memory_api_status(request: Request, _=Depends(require_auth)):
    substrate = _get_substrate()
    if substrate is None:
        return JSONResponse({"error": "substrate unavailable"}, status_code=503)
    try:
        return JSONResponse({
            "status": substrate.status(),
            "events": substrate.audit_events(limit=20),
            "ts": time.time(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
