import json
import time
import asyncio
import logging
from urllib.parse import quote
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from src.database import SessionLocal
from src.models import TaskRun, ApiKey
from src.web.auth import require_auth
from src.web.ratelimit import RateLimiter, ConcurrencyGuard, client_ip

logger = logging.getLogger(__name__)
router = APIRouter()
from src.web.templating import templates

# Bound the prompt so a single request cannot push an unbounded payload into the
# (expensive) multi-provider fan-out. Canonical limit lives in the governed
# service so REST and MCP enforce the same ceiling.
from src.swarm.service import MAX_PROMPT_LEN, llm_providers_available

# Cost/abuse controls for the expensive LLM fan-out endpoint.
# - Per-IP rate limit: a small burst per minute.
# - Global concurrency guard: cap simultaneous in-flight runs across all callers
#   so a few clients cannot saturate provider quotas / the event loop.
_run_limiter = RateLimiter(max_events=5, window_seconds=60)
_run_guard = ConcurrencyGuard(max_concurrent=2)


def _flash(msg: str) -> str:
    """URL-encode a user-facing flash message for a redirect query string."""
    return quote(msg, safe="")


def _has_active_keys():
    db = SessionLocal()
    try:
        return db.query(ApiKey).filter(ApiKey.is_active == True).count() > 0
    finally:
        db.close()


def _llm_providers_available() -> bool:
    """True if at least one enabled LLM provider has its required secrets present.

    Distinct from :func:`_has_active_keys` (which only counts vault rows): this
    confirms the swarm actually has a usable provider, so we can degrade with a
    clear message instead of letting the orchestrator raise opaquely. Delegates to
    the governed service so REST and MCP share one availability definition.
    """
    return llm_providers_available()


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
    from src.swarm.jurisdiction import JURISDICTION_DOMAINS
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_tasks": recent,
        "has_keys": has_keys,
        "result_data": result_data,
        "parsed": _parse_result(result_data),
        "err": request.query_params.get("err"),
        "domains": list(JURISDICTION_DOMAINS),
    })


@router.post("/tasks/run")
async def run_task(
    request: Request,
    prompt: str = Form(""),
    domain: str = Form("general"),
    team: str = Form("default"),
    auth=Depends(require_auth),
):
    from src.swarm.jurisdiction import normalize_domain, normalize_team
    prompt = prompt.strip()
    domain = normalize_domain(domain)
    team = normalize_team(team)
    if not prompt:
        return RedirectResponse(url="/?err=" + _flash("Please enter a prompt"), status_code=303)

    if len(prompt) > MAX_PROMPT_LEN:
        return RedirectResponse(
            url="/?err=" + _flash("Prompt is too long (max %d characters)." % MAX_PROMPT_LEN),
            status_code=303,
        )

    # Per-IP burst limit on the expensive endpoint.
    allowed, retry_after = _run_limiter.check(client_ip(request))
    if not allowed:
        return RedirectResponse(
            url="/?err=" + _flash("Rate limit reached. Try again in %d seconds." % retry_after),
            status_code=303,
        )

    if not _has_active_keys():
        return RedirectResponse(
            url="/?err=" + _flash("No API keys configured. Please add keys first."),
            status_code=303,
        )

    # Graceful degradation: if no enabled LLM provider has its secret present,
    # say so plainly instead of letting the orchestrator raise opaquely.
    if not _llm_providers_available():
        return RedirectResponse(
            url="/?err=" + _flash(
                "No LLM provider is configured. Add a provider key (e.g. OpenAI, "
                "Anthropic) in the API Vault, then try again."
            ),
            status_code=303,
        )

    # Global concurrency guard: reject (don't queue) when the swarm is already
    # saturated so callers get immediate, honest feedback.
    if not _run_guard.try_acquire():
        return RedirectResponse(
            url="/?err=" + _flash("The swarm is busy with other tasks. Please try again shortly."),
            status_code=303,
        )

    db = SessionLocal()
    task_run = TaskRun(
        prompt=prompt, status="running",
        actor_user_id=auth.get("uid"), actor_username=auth.get("username"),
    )
    try:
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        task_id = task_run.id
    except Exception as e:
        db.rollback()
        db.close()
        _run_guard.release()
        logger.error("Failed to create task run: %s", e)
        return RedirectResponse(
            url="/?err=" + _flash("Could not start the task. Please try again."),
            status_code=303,
        )

    start_time = time.time()
    try:
        # Single governed path shared with the SPA endpoint and the MCP server.
        # Providers were already verified above, so skip the re-check here.
        from src.swarm.service import run_governed_swarm, normalize_scores

        result = await run_governed_swarm(
            prompt, domain=domain, team=team, task_run_id=task_id, check_providers=False
        )

        elapsed_ms = int((time.time() - start_time) * 1000)
        result_text = json.dumps(result, indent=2, default=str)
        scores = normalize_scores(result)

        task_run.result = result_text
        task_run.status = "completed"
        task_run.execution_time_ms = elapsed_ms
        task_run.provider_scores = json.dumps(scores) if scores else None
        db.commit()
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        # Log the full error server-side; never surface raw exception text (which
        # may contain provider details) to the user.
        logger.exception("Task execution failed for task %s", task_id)
        task_run.result = "Error: task execution failed. See server logs for details."
        task_run.status = "failed"
        task_run.execution_time_ms = elapsed_ms
        try:
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
        _run_guard.release()

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
