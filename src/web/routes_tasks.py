import json
import time
import asyncio
import logging
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from src.database import SessionLocal
from src.models import TaskRun, ApiKey
from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _has_active_keys():
    db = SessionLocal()
    try:
        return db.query(ApiKey).filter(ApiKey.is_active == True).count() > 0
    finally:
        db.close()


def _parse_result(task):
    """Parse a TaskRun.result JSON string into a dict for structured rendering.

    Returns None when the result is missing or not a JSON object (e.g. a plain
    error string), so templates can fall back to raw display.
    """
    if not task or not task.result:
        return None
    try:
        data = json.loads(task.result)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        recent = db.query(TaskRun).order_by(TaskRun.created_at.desc()).limit(5).all()
    finally:
        db.close()
    has_keys = _has_active_keys()
    result = request.query_params.get("result_id")
    result_data = None
    if result:
        db = SessionLocal()
        try:
            result_data = db.query(TaskRun).filter(TaskRun.id == int(result)).first()
        except Exception:
            pass
        finally:
            db.close()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_tasks": recent,
        "has_keys": has_keys,
        "result_data": result_data,
        "parsed": _parse_result(result_data),
        "err": request.query_params.get("err"),
    })


@router.post("/tasks/run")
async def run_task(request: Request, prompt: str = Form(...), _=Depends(require_auth)):
    if not prompt.strip():
        return RedirectResponse(url="/?err=Please enter a prompt", status_code=303)

    if not _has_active_keys():
        return RedirectResponse(url="/?err=No API keys configured. Please add keys first.", status_code=303)

    db = SessionLocal()
    task_run = TaskRun(prompt=prompt.strip(), status="running")
    try:
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        task_id = task_run.id
    except Exception as e:
        db.rollback()
        db.close()
        return RedirectResponse(url=f"/?err=Failed to create task: {e}", status_code=303)

    start_time = time.time()
    try:
        from src.swarm.broker import CapabilityBroker
        from src.swarm.policy import PolicyEngine
        from src.swarm.integrations import Integrations
        from src.swarm.orchestrator import WaveOrchestrator

        policy = PolicyEngine()
        integrations = Integrations()
        broker = CapabilityBroker(policy, integrations)
        orchestrator = WaveOrchestrator(broker)
        result = await orchestrator.run(prompt.strip())

        elapsed_ms = int((time.time() - start_time) * 1000)
        result_text = json.dumps(result, indent=2, default=str)

        scores = {}
        if "COMPLIANCE_SCORES" in result:
            scores["compliance"] = result["COMPLIANCE_SCORES"]
        if "HALLUCINATION_RISK" in result:
            scores["hallucination_risk"] = result["HALLUCINATION_RISK"]

        task_run.result = result_text
        task_run.status = "completed"
        task_run.execution_time_ms = elapsed_ms
        task_run.provider_scores = json.dumps(scores) if scores else None
        db.commit()
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.error("Task execution failed: %s", e)
        task_run.result = f"Error: {str(e)}"
        task_run.status = "failed"
        task_run.execution_time_ms = elapsed_ms
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/?result_id={task_id}", status_code=303)


@router.get("/tasks/history", response_class=HTMLResponse)
async def task_history(request: Request, _=Depends(require_auth)):
    page = int(request.query_params.get("page", 1))
    per_page = 20
    db = SessionLocal()
    try:
        total = db.query(TaskRun).count()
        tasks = (
            db.query(TaskRun)
            .order_by(TaskRun.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
    finally:
        db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("history.html", {
        "request": request,
        "tasks": tasks,
        "page": page,
        "total_pages": total_pages,
    })


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
    finally:
        db.close()
    if not task:
        return RedirectResponse(url="/tasks/history", status_code=303)

    scores = None
    if task.provider_scores:
        try:
            scores = json.loads(task.provider_scores)
        except Exception:
            pass

    return templates.TemplateResponse("task_detail.html", {
        "request": request,
        "task": task,
        "scores": scores,
        "parsed": _parse_result(task),
    })
