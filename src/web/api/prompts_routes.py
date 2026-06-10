"""Prompt-engine API routes — mounted under /api/prompts/...

Operator surface for the self-evolving prompt engine:
  GET  /prompts                       — list optimizable programs
  GET  /prompts/{name}                — program detail (versions + runs)
  POST /prompts/{name}/optimize       — launch an optimization run (async)
  GET  /prompts/runs/{run_id}         — poll an optimization run
  POST /prompts/versions/{id}/promote — raise a governance proposal to go live
  POST /prompts/versions/{id}/rollback— audited revert to a previously-live version

Promotion never bypasses governance: /promote only RAISES a proposal; the version
goes live solely through the existing vote/enactment gate.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException

from src.web.auth import require_auth
from src.promptopt import store
from src.promptopt import service
from src.promptopt.service import NotEnoughTraces, RunInProgress

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prompts")


@router.get("")
async def list_prompts(_: bool = Depends(require_auth)):
    return {"programs": store.list_programs(), "min_traces": service.min_traces()}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, _: bool = Depends(require_auth)):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run %d not found" % run_id)
    return run


@router.get("/{name}")
async def prompt_detail(name: str, _: bool = Depends(require_auth)):
    detail = store.get_program_detail(name)
    if detail is None:
        raise HTTPException(status_code=404, detail="program '%s' not found" % name)
    return detail


@router.post("/{name}/optimize")
async def optimize(name: str, _: bool = Depends(require_auth)):
    try:
        run_id = service.launch_run(name, started_by="operator")
    except RunInProgress as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NotEnoughTraces as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "run_id": run_id}


@router.post("/versions/{version_id}/promote")
async def promote(version_id: int, _: bool = Depends(require_auth)):
    try:
        result = store.propose_promotion(version_id, operator="operator")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning("propose_promotion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "ok": True,
        "proposal_id": result["proposal_id"],
        "proposal_db_id": result["proposal_db_id"],
        "message": "Promotion proposal raised (track=policy). It goes live only "
                   "once enacted via the Governance gate.",
    }


@router.post("/versions/{version_id}/rollback")
async def rollback(version_id: int, _: bool = Depends(require_auth)):
    try:
        result = store.rollback_to(version_id, operator="operator")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning("rollback failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, **result}
