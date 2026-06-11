"""Evolution loop API routes — mounted under /api/evolution/...

Operator surface for the edge-truth / thesis self-evolution loop:
  GET  /evolution/components           — list all cognition components
  GET  /evolution/components/{id}      — component detail (theses + cycle log)
  POST /evolution/components/{id}/start — enable + start loop for component
  POST /evolution/components/{id}/stop  — disable + stop loop for component
  GET  /evolution/theses               — list theses (optionally filtered)
  GET  /evolution/theses/{id}          — thesis detail with trial iterations
  POST /evolution/components/{id}/thesis — manually seed a thesis for testing
  POST /evolution/components/{id}/trial  — run a single manual trial cycle
  GET  /evolution/loop/status          — global loop status across all components
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evolution")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_component_or_404(component_id: int):
    from src.promptopt import evolution_store as estore
    from src.database import SessionLocal
    from src.models import CognitionComponent
    db = SessionLocal()
    try:
        c = estore.get_component_by_id(db, component_id)
        if c is None:
            raise HTTPException(status_code=404, detail="component %d not found" % component_id)
        return c
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@router.get("/components")
async def list_components(_: bool = Depends(require_auth)):
    from src.promptopt import evolution_store as estore
    from src.promptopt.evolution import get_loop
    loop = get_loop()
    rows = estore.list_components()
    return {
        "components": [{**r, "thread_alive": loop.is_running(r["id"])} for r in rows]
    }


@router.get("/components/{component_id}")
async def get_component(component_id: int, _: bool = Depends(require_auth)):
    from src.promptopt import evolution_store as estore
    from src.promptopt.evolution import get_loop
    detail = estore.get_component_detail(component_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="component %d not found" % component_id)
    loop = get_loop()
    detail["component"]["thread_alive"] = loop.is_running(component_id)
    return detail


@router.get("/components/{component_id}/status")
async def get_component_status(component_id: int, _: bool = Depends(require_auth)):
    """Lightweight status snapshot for cheap live polling (no full detail)."""
    from src.promptopt import evolution_store as estore
    from src.promptopt.evolution import get_loop
    status = estore.get_component_status(component_id)
    if status is None:
        raise HTTPException(status_code=404, detail="component %d not found" % component_id)
    status["thread_alive"] = get_loop().is_running(component_id)
    return status


@router.post("/components/{component_id}/start")
async def start_loop(component_id: int, _: bool = Depends(require_auth)):
    from src.promptopt import evolution_store as estore
    from src.promptopt.evolution import get_loop
    _get_component_or_404(component_id)
    estore.set_loop_enabled(component_id, True)
    started = get_loop().start_component(component_id)
    return {"ok": True, "started": started, "already_running": not started}


@router.post("/components/{component_id}/stop")
async def stop_loop(component_id: int, _: bool = Depends(require_auth)):
    from src.promptopt import evolution_store as estore
    from src.promptopt.evolution import get_loop
    _get_component_or_404(component_id)
    estore.set_loop_enabled(component_id, False)
    stopped = get_loop().stop_component(component_id)
    return {"ok": True, "stopped": stopped}


# ---------------------------------------------------------------------------
# Theses
# ---------------------------------------------------------------------------

@router.get("/theses")
async def list_theses(
    component_id: Optional[int] = None,
    status: Optional[str] = None,
    _: bool = Depends(require_auth),
):
    from src.promptopt import evolution_store as estore
    rows = estore.list_theses(component_id=component_id, status=status)
    return {"theses": rows, "total": len(rows)}


@router.get("/theses/{thesis_id}")
async def get_thesis(thesis_id: int, _: bool = Depends(require_auth)):
    from src.promptopt import evolution_store as estore
    t = estore.get_thesis(thesis_id)
    if t is None:
        raise HTTPException(status_code=404, detail="thesis %d not found" % thesis_id)
    return t


# ---------------------------------------------------------------------------
# Manual thesis seeding
# ---------------------------------------------------------------------------

class SeedThesisRequest(BaseModel):
    branch_cell: str
    thesis: Dict[str, Any]
    rationale: str = ""
    requires_human_gate: bool = False


@router.post("/components/{component_id}/thesis")
async def seed_thesis(
    component_id: int,
    body: SeedThesisRequest,
    _: bool = Depends(require_auth),
):
    from src.promptopt import evolution_store as estore
    from src.database import SessionLocal
    from src.models import CognitionComponent

    _get_component_or_404(component_id)

    db = SessionLocal()
    try:
        c = estore.get_component_by_id(db, component_id)
        valid_cells = []
        from src.promptopt.evolution import BRANCH_CELLS_BY_TYPE
        valid_cells = BRANCH_CELLS_BY_TYPE.get(c.component_type, [])
    finally:
        db.close()

    if valid_cells and body.branch_cell not in valid_cells:
        raise HTTPException(
            status_code=400,
            detail="branch_cell '%s' not valid for type '%s'. Valid: %s"
            % (body.branch_cell, "?", ", ".join(valid_cells)),
        )

    db = SessionLocal()
    try:
        c = estore.get_component_by_id(db, component_id)
        cycle_index = (c.cycle_count or 0) + 1
    finally:
        db.close()

    thesis_id = estore.create_thesis(
        component_id=component_id,
        branch_cell=body.branch_cell,
        thesis=body.thesis,
        rationale=body.rationale or "manually seeded thesis",
        cycle_index=cycle_index,
        requires_human_gate=body.requires_human_gate,
        selection_votes={"source": "manual"},
    )
    return {"ok": True, "thesis_id": thesis_id}


# ---------------------------------------------------------------------------
# Manual trial trigger
# ---------------------------------------------------------------------------

@router.post("/components/{component_id}/trial")
async def trigger_trial(component_id: int, _: bool = Depends(require_auth)):
    """Run a single manual trial cycle (propose → test → consensus → promote/reject)."""
    from src.promptopt.evolution import _run_cycle, get_loop
    from src.promptopt import evolution_store as estore

    _get_component_or_404(component_id)

    if get_loop().is_running(component_id):
        raise HTTPException(
            status_code=409,
            detail="Loop is already running for this component. Stop it first or let the loop handle cycling."
        )

    import asyncio
    try:
        outcome = await asyncio.to_thread(_run_cycle, component_id, True)
    except Exception as e:
        logger.exception("Manual trial failed for component %d", component_id)
        raise HTTPException(status_code=500, detail="Trial failed: %s" % str(e))

    return {"ok": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# Global loop status
# ---------------------------------------------------------------------------

@router.get("/loop/status")
async def loop_status(_: bool = Depends(require_auth)):
    from src.promptopt.evolution import get_loop
    return {
        "components": get_loop().status_all(),
    }
