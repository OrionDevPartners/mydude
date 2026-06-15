"""JSON API router — all endpoints return JSON for the React SPA.

Every route mirrors an existing Jinja2 route but returns structured JSON instead
of HTML. Authentication uses the same cookie-session mechanism so the browser's
existing session_token cookie is honoured.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile,
)
from fastapi.responses import JSONResponse

from src.web.auth import (
    MAX_PASSWORD_LEN,
    MAX_USERNAME_LEN,
    SESSION_MAX_AGE,
    _dev_auth_bypass_enabled,
    _login_failures,
    _serializer,
    _set_session_cookie,
    authenticate_user,
    client_ip,
    hash_password,
    make_session_token,
    require_admin,
    require_auth,
    resolve_session,
)
from src.web.branding import PRODUCT
from src.web.ratelimit import client_ip

from src.fleet.api_routes import router as fleet_router
from src.web.api.prompts_routes import router as prompts_router
from src.web.api.evolution_routes import router as evolution_router
from src.telephony.api_routes import router as telephony_router
from src.web.api.capabilities_routes import router as capabilities_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
router.include_router(fleet_router)
router.include_router(prompts_router)
router.include_router(evolution_router)
router.include_router(telephony_router)
router.include_router(capabilities_router)

import secrets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_ok(**kwargs):
    return {"ok": True, **kwargs}


def _json_err(msg: str, status: int = 400):
    raise HTTPException(status_code=status, detail=msg)


def _dt(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _clip(val, n: int = 280) -> str | None:
    """Defensively truncate a free-text audit field before it reaches the UI."""
    if val is None:
        return None
    s = str(val)
    return s if len(s) <= n else s[: n - 1] + "…"


def _epoch_to_iso(val) -> str | None:
    """Format an epoch-second float (or datetime) as an ISO 8601 string."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    try:
        ts = float(val)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.utcfromtimestamp(ts).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _date_to_epoch(val: str, end_of_day: bool = False) -> float | None:
    """Parse a 'YYYY-MM-DD' (or full ISO) date string into an epoch second.

    Returns None for empty/unparseable input so callers can treat it as "no
    bound". When ``end_of_day`` is set, a bare date resolves to 23:59:59.999999
    of that day so a ``before`` filter is inclusive of the whole day.
    """
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
    except ValueError:
        try:
            dt = datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return None
    if end_of_day and len(val) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Auth / Branding
# ---------------------------------------------------------------------------

@router.get("/branding")
async def branding():
    return PRODUCT


@router.post("/login")
async def api_login(request: Request, username: str = Form(""), password: str = Form("")):
    from datetime import datetime as _dtnow
    from src.database import SessionLocal
    ip = client_ip(request)
    allowed, retry_after = _login_failures.peek(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in %d seconds." % retry_after,
        )
    if len(password) <= MAX_PASSWORD_LEN and len(username) <= MAX_USERNAME_LEN:
        db = SessionLocal()
        try:
            user = authenticate_user(db, username, password)
            if user is not None:
                _login_failures.reset(ip)
                user.last_login_at = _dtnow.utcnow()
                db.commit()
                token = make_session_token(user)
                resp = JSONResponse({"ok": True, "username": user.username, "is_admin": bool(user.is_admin)})
                _set_session_cookie(resp, token)
                return resp
        finally:
            db.close()
    _login_failures.record(ip)
    raise HTTPException(status_code=401, detail="Invalid username or password")


@router.post("/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_token")
    return resp


@router.get("/me")
async def api_me(request: Request):
    data = resolve_session(request)
    if data is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "authenticated": True,
        "username": data.get("username"),
        "is_admin": bool(data.get("is_admin")),
        "dev_bypass": bool(data.get("dev_bypass")),
    }


@router.get("/auth/dev-info")
async def api_auth_dev_info():
    """Public endpoint — tells the login page whether the developer bypass is
    available.  Returns ``available: true`` only outside a deployment so the
    button is never rendered on the live production site.
    """
    from src.web.auth import _in_deployment
    return {"available": not _in_deployment()}


@router.post("/auth/dev-login")
async def api_auth_dev_login(request: Request):
    """One-click developer sign-in for the workspace / dev environment.

    Double-gated:
    - Returns 403 when ``REPLIT_DEPLOYMENT=1`` — the endpoint is inert in production.
    - Issues a signed session cookie carrying the dev-bypass identity so the
      React SPA recognises the user as authenticated without a password.
    """
    from src.web.auth import _in_deployment, make_dev_session_token, _set_session_cookie
    if _in_deployment():
        raise HTTPException(
            status_code=403,
            detail="Developer login is not available in the production deployment.",
        )
    token = make_dev_session_token()
    resp = JSONResponse({"ok": True, "username": "dev-bypass", "is_admin": True, "dev_bypass": True})
    _set_session_cookie(resp, token)
    return resp


def _actor(auth: dict) -> dict:
    """Audit attribution for the currently authenticated identity."""
    return {
        "actor_user_id": auth.get("uid"),
        "actor_username": auth.get("username"),
    }


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------

def _user_dict(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email or "",
        "is_active": bool(u.is_active),
        "is_admin": bool(u.is_admin),
        "created_at": _dt(u.created_at),
        "last_login_at": _dt(u.last_login_at),
    }


@router.get("/users")
async def api_users(auth=Depends(require_admin)):
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.asc()).all()
        return {"users": [_user_dict(u) for u in users]}
    finally:
        db.close()


@router.post("/users")
async def api_create_user(
    username: str = Form(""),
    password: str = Form(""),
    email: str = Form(""),
    is_admin: str = Form(""),
    auth=Depends(require_admin),
):
    from src.database import SessionLocal
    from src.models import User
    from sqlalchemy import func

    username = username.strip()
    email = email.strip()
    make_admin = is_admin.lower() in ("1", "true", "yes", "on")

    if not username or len(username) > MAX_USERNAME_LEN:
        raise HTTPException(400, "Username is required and must be under %d characters." % MAX_USERNAME_LEN)
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(password) > MAX_PASSWORD_LEN:
        raise HTTPException(400, "Password is too long.")
    if len(email) > 255:
        raise HTTPException(400, "Email is too long.")

    db = SessionLocal()
    try:
        clash = db.query(User).filter(func.lower(User.username) == username.lower()).first()
        if clash:
            raise HTTPException(409, "That username is already taken.")
        if email:
            eclash = db.query(User).filter(func.lower(User.email) == email.lower()).first()
            if eclash:
                raise HTTPException(409, "That email is already in use.")
        user = User(
            username=username,
            email=email or None,
            password_hash=hash_password(password),
            is_active=True,
            is_admin=make_admin,
        )
        db.add(user)
        db.commit()
        return {"ok": True, "user": _user_dict(user)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Failed to create user: %s" % e)
    finally:
        db.close()


@router.post("/users/{user_id}/toggle")
async def api_toggle_user(user_id: int, auth=Depends(require_admin)):
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found.")
        if user.id == auth.get("uid"):
            raise HTTPException(400, "You cannot deactivate your own account.")
        if user.is_active and user.is_admin:
            active_admins = db.query(User).filter(User.is_admin == True, User.is_active == True).count()  # noqa: E712
            if active_admins <= 1:
                raise HTTPException(400, "Cannot deactivate the last active admin.")
        user.is_active = not user.is_active
        db.commit()
        return {"ok": True, "is_active": user.is_active}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not update user.")
    finally:
        db.close()


@router.post("/users/{user_id}/password")
async def api_reset_user_password(user_id: int, password: str = Form(""), auth=Depends(require_admin)):
    from src.database import SessionLocal
    from src.models import User
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(password) > MAX_PASSWORD_LEN:
        raise HTTPException(400, "Password is too long.")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found.")
        user.password_hash = hash_password(password)
        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not reset password.")
    finally:
        db.close()


@router.post("/users/{user_id}/delete")
async def api_delete_user(user_id: int, auth=Depends(require_admin)):
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found.")
        if user.id == auth.get("uid"):
            raise HTTPException(400, "You cannot delete your own account.")
        if user.is_admin:
            active_admins = db.query(User).filter(User.is_admin == True, User.is_active == True).count()  # noqa: E712
            if user.is_active and active_admins <= 1:
                raise HTTPException(400, "Cannot delete the last active admin.")
        db.delete(user)
        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not delete user.")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tasks / Dashboard
# ---------------------------------------------------------------------------

def _parse_task(task) -> dict:
    parsed = None
    if task.result:
        try:
            d = json.loads(task.result)
            parsed = d if isinstance(d, dict) else None
        except Exception:
            pass
    scores = None
    if task.provider_scores:
        try:
            scores = json.loads(task.provider_scores)
        except Exception:
            pass
    return {
        "id": task.id,
        "prompt": task.prompt,
        "status": task.status,
        "result": task.result,
        "parsed": parsed,
        "scores": scores,
        "execution_time_ms": task.execution_time_ms,
        "created_at": _dt(task.created_at),
    }


@router.get("/dashboard")
async def api_dashboard(request: Request, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import TaskRun, ApiKey
    db = SessionLocal()
    try:
        recent = db.query(TaskRun).order_by(TaskRun.created_at.desc()).limit(5).all()
        has_keys = db.query(ApiKey).filter(ApiKey.is_active == True).count() > 0
    finally:
        db.close()
    from src.swarm.jurisdiction import JURISDICTION_DOMAINS
    return {
        "recent_tasks": [_parse_task(t) for t in recent],
        "has_keys": has_keys,
        "domains": list(JURISDICTION_DOMAINS),
    }


# Strong references to in-flight background task-run coroutines. asyncio only
# holds a weak reference to scheduled tasks, so without this they could be
# garbage-collected mid-run; the done callback discards each when it finishes.
_background_task_runs: set = set()


def _fail_task_run(task_id: int, message: str) -> None:
    """Mark a TaskRun row as failed with the given message (best effort)."""
    from src.database import SessionLocal
    from src.models import TaskRun

    db = SessionLocal()
    try:
        task_run = db.query(TaskRun).filter(TaskRun.id == task_id).first()
        if task_run is None:
            return
        task_run.result = message
        task_run.status = "failed"
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to mark task %s as failed", task_id)
    finally:
        db.close()


async def _execute_task_run(task_id: int, prompt: str, domain: str, team: str) -> None:
    """Run the swarm orchestration for an already-created TaskRun row.

    Runs in the background so /api/tasks/run can return immediately. Owns its
    own DB session, transitions the row to completed/failed, and always
    releases the concurrency guard so a crashed run never leaks a guard slot or
    leaves an orphaned "running" row.
    """
    import time
    from src.database import SessionLocal
    from src.models import TaskRun
    from src.web.routes_tasks import _run_guard

    db = SessionLocal()
    start_time = time.time()
    try:
        task_run = db.query(TaskRun).filter(TaskRun.id == task_id).first()
        if task_run is None:
            logger.error("Background task run %s vanished before execution", task_id)
            return
        try:
            # Single governed path shared with the legacy form post and the MCP
            # server. Providers were already verified in api_run_task before the
            # row/guard were taken, so skip the re-check here.
            from src.swarm.service import run_governed_swarm, normalize_scores

            result = await run_governed_swarm(
                prompt, domain=domain, team=team, task_run_id=task_id, check_providers=False
            )

            elapsed_ms = int((time.time() - start_time) * 1000)
            result_text = json.dumps(result, indent=2, default=str)
            # Compact, display-ready governance summary (compliance + hallucination
            # as 0..1, jurisdiction string, benchmark routing record).
            scores = normalize_scores(result)
            task_run.result = result_text
            task_run.status = "completed"
            task_run.execution_time_ms = elapsed_ms
            task_run.provider_scores = json.dumps(scores) if scores else None
            db.commit()
        except Exception:
            elapsed_ms = int((time.time() - start_time) * 1000)
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


@router.post("/tasks/run")
async def api_run_task(
    request: Request,
    prompt: str = Form(""),
    domain: str = Form("general"),
    team: str = Form("default"),
    _=Depends(require_auth),
):
    import time
    from src.database import SessionLocal
    from src.models import TaskRun, ApiKey
    from src.web.ratelimit import RateLimiter, ConcurrencyGuard
    from src.swarm.jurisdiction import normalize_domain, normalize_team
    from src.swarm.service import MAX_PROMPT_LEN

    prompt = prompt.strip()
    domain = normalize_domain(domain)
    team = normalize_team(team)
    if not prompt:
        raise HTTPException(status_code=400, detail="Please enter a prompt")
    if len(prompt) > MAX_PROMPT_LEN:
        raise HTTPException(status_code=400, detail="Prompt is too long (max %d characters)." % MAX_PROMPT_LEN)

    from src.web.routes_tasks import _run_limiter, _run_guard, _has_active_keys, _llm_providers_available
    allowed, retry_after = _run_limiter.check(client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit reached. Try again in %d seconds." % retry_after)
    if not _has_active_keys():
        raise HTTPException(status_code=400, detail="No API keys configured. Please add keys first.")
    if not _llm_providers_available():
        raise HTTPException(status_code=400, detail="No LLM provider is configured. Add a provider key in the API Vault.")
    if not _run_guard.try_acquire():
        raise HTTPException(status_code=429, detail="The swarm is busy with other tasks. Please try again shortly.")

    db = SessionLocal()
    try:
        task_run = TaskRun(prompt=prompt, status="running")
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        task_id = task_run.id
    except Exception as e:
        db.rollback()
        _run_guard.release()
        raise HTTPException(status_code=500, detail="Could not start the task. Please try again.")
    finally:
        db.close()

    # Kick off the (potentially multi-minute) swarm run in the background and
    # return immediately so the polling UI (getTask) drives completion instead
    # of holding the HTTP request open past proxy/client timeouts. The
    # background runner owns the concurrency-guard release.
    try:
        bg = asyncio.create_task(_execute_task_run(task_id, prompt, domain, team))
        _background_task_runs.add(bg)
        bg.add_done_callback(_background_task_runs.discard)
    except Exception:
        # Scheduling failed: mark the row failed and release the guard so we
        # never leave an orphaned "running" row or a leaked guard slot.
        logger.exception("Failed to schedule background task %s", task_id)
        _fail_task_run(task_id, "Error: task execution could not be started. See server logs for details.")
        _run_guard.release()
        raise HTTPException(status_code=500, detail="Could not start the task. Please try again.")

    return {"ok": True, "task_id": task_id, "status": "running"}


@router.get("/tasks/history")
async def api_task_history(request: Request, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import TaskRun
    page = max(1, int(request.query_params.get("page", 1) or 1))
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
        task_list = [_parse_task(t) for t in tasks]
    finally:
        db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {"tasks": task_list, "page": page, "total_pages": total_pages, "total": total}


@router.get("/tasks/{task_id}")
async def api_task_detail(task_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import TaskRun
    db = SessionLocal()
    try:
        task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return _parse_task(task)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API Key Vault
# ---------------------------------------------------------------------------

@router.get("/keys")
async def api_keys(request: Request, auth=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ApiKey
    from src.web.crypto import decrypt_value, mask_key
    from src.web.service_catalog import SERVICE_CATALOG, CATEGORIES, get_service
    from src.web.routes_keys import _reminders, _resolve_env_var

    q = (request.query_params.get("q") or "").strip().lower()
    cat = (request.query_params.get("category") or "").strip()
    reveal_id = request.query_params.get("reveal")
    try:
        reveal_id = int(reveal_id) if reveal_id else None
    except ValueError:
        reveal_id = None

    db = SessionLocal()
    try:
        all_keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        reminders = _reminders([k for k in all_keys if k.is_active])

        revealed_value = None
        if reveal_id:
            from src.models import KeyAuditLog
            target = db.query(ApiKey).filter(ApiKey.id == reveal_id).first()
            if target:
                try:
                    revealed_value = decrypt_value(target.encrypted_key)
                    db.add(KeyAuditLog(
                        api_key_id=target.id, provider=target.provider,
                        label=target.label, action="reveal", detail="Key value revealed in UI",
                        **_actor(auth),
                    ))
                    db.commit()
                except Exception:
                    pass

        key_list = []
        for k in all_keys:
            svc = get_service(k.provider)
            try:
                raw = decrypt_value(k.encrypted_key)
                masked = mask_key(raw)
            except Exception:
                raw = None
                masked = "••••••••(error)"
            entry = {
                "id": k.id,
                "provider": k.provider,
                "name": svc["name"] if svc else k.provider,
                "label": k.label or "",
                "category": k.category or (svc["category"] if svc else "Other"),
                "masked_key": masked,
                "revealed": raw if (reveal_id == k.id) else None,
                "env_var": _resolve_env_var(k) or "",
                "is_active": k.is_active,
                "notes": k.notes or "",
                "expires_at": _dt(k.expires_at),
                "rotation_days": k.rotation_days,
                "last_used_at": _dt(k.last_used_at),
                "created_at": _dt(k.created_at),
            }
            if q:
                hay = " ".join([entry["provider"], entry["name"], entry["label"], entry["category"], entry["env_var"]]).lower()
                if q not in hay:
                    continue
            if cat and entry["category"] != cat:
                continue
            key_list.append(entry)

        used_categories = sorted({(k.category or (get_service(k.provider)["category"] if get_service(k.provider) else "Other")) for k in all_keys})
    finally:
        db.close()

    from src.web.crypto import encryption_key_is_persistent
    resp = JSONResponse({
        "keys": key_list,
        "catalog": SERVICE_CATALOG,
        "categories": CATEGORIES,
        "used_categories": used_categories,
        "reminders": reminders,
        "total_count": len(all_keys),
        "encryption_persistent": encryption_key_is_persistent(),
    })
    if revealed_value is not None:
        resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/keys")
async def api_add_key(
    request: Request,
    provider: str = Form(""),
    label: str = Form(""),
    api_key: str = Form(""),
    category: str = Form(""),
    env_var: str = Form(""),
    notes: str = Form(""),
    expires_at: str = Form(""),
    rotation_days: str = Form(""),
    auth=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import ApiKey, KeyAuditLog
    from src.web.crypto import encrypt_value
    from src.web.service_catalog import env_var_for, category_for
    from src.web.routes_keys import (
        MAX_PROVIDER_LEN, MAX_LABEL_LEN, MAX_API_KEY_LEN,
        MAX_CATEGORY_LEN, MAX_ENV_VAR_LEN, MAX_NOTES_LEN, MAX_ROTATION_DAYS,
    )

    provider = provider.lower().strip()
    api_key = api_key.strip()
    if not provider or len(provider) > MAX_PROVIDER_LEN:
        raise HTTPException(400, "Provider is required and must be under %d characters." % MAX_PROVIDER_LEN)
    if not api_key:
        raise HTTPException(400, "API key value is required.")
    if len(api_key) > MAX_API_KEY_LEN:
        raise HTTPException(400, "API key value is too long.")
    if len(label) > MAX_LABEL_LEN:
        raise HTTPException(400, "Label is too long.")
    if len(notes) > MAX_NOTES_LEN:
        raise HTTPException(400, "Notes are too long.")

    db = SessionLocal()
    try:
        exp = None
        if expires_at.strip():
            try:
                exp = datetime.strptime(expires_at.strip(), "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Expiry date must be YYYY-MM-DD.")
        rot = None
        if rotation_days.strip():
            try:
                rot = int(rotation_days.strip())
            except ValueError:
                raise HTTPException(400, "Rotation days must be a whole number.")
            if rot < 1 or rot > MAX_ROTATION_DAYS:
                raise HTTPException(400, "Rotation days must be 1-%d." % MAX_ROTATION_DAYS)

        new_key = ApiKey(
            provider=provider,
            label=label.strip() or None,
            encrypted_key=encrypt_value(api_key),
            is_active=True,
            category=category.strip() or category_for(provider),
            env_var=env_var.strip() or env_var_for(provider),
            notes=notes.strip() or None,
            expires_at=exp,
            rotation_days=rot,
            last_rotated_at=datetime.utcnow(),
        )
        db.add(new_key)
        db.flush()
        db.add(KeyAuditLog(api_key_id=new_key.id, provider=new_key.provider, label=new_key.label, action="create", detail="Key added to vault", **_actor(auth)))
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Failed to add key: %s" % e)
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return {"ok": True, "msg": "Key saved to vault"}


@router.post("/keys/{key_id}/rotate")
async def api_rotate_key(key_id: int, api_key: str = Form(""), auth=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ApiKey, KeyAuditLog
    from src.web.crypto import encrypt_value
    from src.web.routes_keys import MAX_API_KEY_LEN

    api_key = api_key.strip()
    if not api_key:
        raise HTTPException(400, "New API key value is required.")
    if len(api_key) > MAX_API_KEY_LEN:
        raise HTTPException(400, "API key value is too long.")
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if not key:
            raise HTTPException(404, "Key not found.")
        key.encrypted_key = encrypt_value(api_key)
        key.last_rotated_at = datetime.utcnow()
        db.add(KeyAuditLog(api_key_id=key.id, provider=key.provider, label=key.label, action="rotate", detail="Key value rotated", **_actor(auth)))
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not rotate key.")
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return {"ok": True, "msg": "Key rotated"}


@router.post("/keys/{key_id}/toggle")
async def api_toggle_key(key_id: int, auth=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ApiKey, KeyAuditLog
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if not key:
            raise HTTPException(404, "Key not found.")
        key.is_active = not key.is_active
        db.add(KeyAuditLog(api_key_id=key.id, provider=key.provider, label=key.label,
                           action="enable" if key.is_active else "disable", **_actor(auth)))
        db.commit()
        result = {"ok": True, "is_active": key.is_active}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not toggle key.")
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return result


@router.post("/keys/{key_id}/delete")
async def api_delete_key(key_id: int, auth=Depends(require_auth)):
    import os
    from src.database import SessionLocal
    from src.models import ApiKey, KeyAuditLog
    from src.web.routes_keys import _resolve_env_var
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if not key:
            raise HTTPException(404, "Key not found.")
        ev = _resolve_env_var(key)
        if ev:
            os.environ.pop(ev, None)
        db.add(KeyAuditLog(provider=key.provider, label=key.label, action="delete", detail="Key removed from vault", **_actor(auth)))
        db.delete(key)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not delete key.")
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return {"ok": True, "msg": "Key deleted"}


@router.get("/keys/audit")
async def api_key_audit(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import KeyAuditLog
    db = SessionLocal()
    try:
        logs = db.query(KeyAuditLog).order_by(KeyAuditLog.created_at.desc()).limit(200).all()
        entries = [{
            "provider": l.provider or "-",
            "label": l.label or "",
            "action": l.action,
            "detail": l.detail or "",
            "actor": l.actor_username or "—",
            "created_at": _dt(l.created_at),
        } for l in logs]
    finally:
        db.close()
    return {"entries": entries}


@router.get("/audit")
async def api_system_audit(auth=Depends(require_auth)):
    """General system action audit trail — governance control actions such as
    cloud-shift toggles and swarm metric resets. Admins see every entry; other
    users see only their own. Free-text fields are truncated defensively and
    never carry secrets."""
    from src.database import SessionLocal
    from src.models import AuditLog, User
    db = SessionLocal()
    try:
        q = db.query(AuditLog)
        if not auth.get("is_admin"):
            q = q.filter(AuditLog.user_id == auth.get("uid"))
        logs = q.order_by(AuditLog.created_at.desc()).limit(200).all()
        uids = {l.user_id for l in logs if l.user_id is not None}
        names: dict = {}
        if uids:
            for u in db.query(User).filter(User.id.in_(uids)).all():
                names[u.id] = u.username
        entries = [{
            "id": l.id,
            "user": names.get(l.user_id) or (str(l.user_id) if l.user_id is not None else "—"),
            "command": l.command,
            "args": _clip(l.args),
            "status": l.status or "ok",
            "output_preview": _clip(l.output_preview),
            "created_at": _dt(l.created_at),
        } for l in logs]
    finally:
        db.close()
    return {"entries": entries}


# ---------------------------------------------------------------------------
# Services / Directory / Connected
# ---------------------------------------------------------------------------

@router.get("/directory")
async def api_directory(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ApiKey
    from src.web.service_catalog import manual_services
    db = SessionLocal()
    try:
        saved = {k.provider for k in db.query(ApiKey).all()}
    finally:
        db.close()
    services = [{**svc, "saved": svc["slug"] in saved} for svc in manual_services()]
    grouped = {}
    for svc in services:
        grouped.setdefault(svc["category"], []).append(svc)
    grouped_list = [{"category": k, "services": v} for k, v in sorted(grouped.items())]
    return {"grouped": grouped_list}


@router.get("/connected")
async def api_connected(_=Depends(require_auth)):
    from src.web.service_catalog import connector_services
    from src.web.connectors import get_connection_status, proxy_available
    services = connector_services()
    names = [s["connector"] for s in services]
    status = get_connection_status(names) if proxy_available() else {}
    rows = []
    for svc in services:
        st = status.get(svc["connector"], {})
        rows.append({
            "name": svc["name"],
            "category": svc["category"],
            "connector": svc["connector"],
            "connected": bool(st.get("connected")),
            "created_at": st.get("created_at"),
            "description": svc.get("description"),
        })
    rows.sort(key=lambda r: (not r["connected"], r["category"], r["name"]))
    connected_count = sum(1 for r in rows if r["connected"])
    return {
        "rows": rows,
        "proxy_available": proxy_available(),
        "connected_count": connected_count,
        "total_count": len(rows),
    }


# ---------------------------------------------------------------------------
# Governance / Provenance / Memory / System
# ---------------------------------------------------------------------------

@router.get("/governance")
async def api_governance(request: Request, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import SentinelEvent, PerformanceLedgerEntry, ProviderMetric
    from sqlalchemy import func, Integer
    db = SessionLocal()
    try:
        alerts_q = db.query(SentinelEvent).order_by(SentinelEvent.acknowledged.asc(), SentinelEvent.created_at.desc()).limit(50).all()
        open_alerts = db.query(SentinelEvent).filter(SentinelEvent.acknowledged == False).count()
        ledger_q = db.query(PerformanceLedgerEntry).order_by(PerformanceLedgerEntry.created_at.desc()).limit(25).all()
        metrics_rows = db.query(
            ProviderMetric.provider,
            func.count(ProviderMetric.id).label("calls"),
            func.avg(ProviderMetric.latency_ms).label("avg_latency"),
            func.sum(func.cast(ProviderMetric.success, Integer)).label("successes"),
            func.avg(ProviderMetric.rating).label("avg_rating"),
        ).group_by(ProviderMetric.provider).all()

        alerts = [{
            "id": a.id, "rule": a.alert_type, "severity": a.severity,
            "detail": " — ".join(
                p for p in (a.description, a.recommended_action) if p
            ) or "",
            "acknowledged": a.acknowledged,
            "created_at": _dt(a.created_at),
        } for a in alerts_q]
        ledger = [{
            "id": l.id, "agent_role": l.agent_role, "provider": l.provider,
            "score": l.score, "detail": l.detail or "", "created_at": _dt(l.created_at),
        } for l in ledger_q]
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

        # Governance proposals (read-only) with quorum + participation progress.
        from src.models import GovernanceProposal
        from src.swarm.governance_engine import GovernanceEngine
        _ge = GovernanceEngine()

        def _ser_proposal(p):
            try:
                tally = _ge._resolve_vote_tally(db, p.id)
            except Exception:
                tally = {
                    "yes": 0.0, "no": 0.0, "abstain": 0.0, "total_effective": 0.0,
                    "participation_weight": 0.0, "yes_ratio": 0.0, "vote_count": 0,
                    "delegation_map": {},
                }
            participation = _ge.participation_status(tally, p.track)
            return {
                "id": p.id, "proposal_id": p.proposal_id, "title": p.title,
                "track": p.track, "origin": p.origin, "status": p.status,
                "proposed_action": p.proposed_action or "",
                "quorum_threshold": p.quorum_threshold or 0.0,
                "yes": tally["yes"], "no": tally["no"], "abstain": tally["abstain"],
                "yes_ratio": tally["yes_ratio"],
                "total_effective": tally["total_effective"],
                "vote_count": tally["vote_count"],
                "participation": participation,
                "created_at": _dt(p.created_at),
            }

        open_props = (
            db.query(GovernanceProposal)
            .filter(GovernanceProposal.status == "open")
            .order_by(GovernanceProposal.created_at.desc())
            .limit(50).all()
        )
        recent_props = (
            db.query(GovernanceProposal)
            .filter(GovernanceProposal.status != "open")
            .order_by(GovernanceProposal.created_at.desc())
            .limit(15).all()
        )
        proposals = [_ser_proposal(p) for p in open_props]
        recent_proposals = [_ser_proposal(p) for p in recent_props]
        open_proposals = len(open_props)

        # Acquisition jobs — loaded here while the session is still live.
        acquisition_jobs = []
        acquisition_enabled = False
        try:
            from src.swarm.policy import PolicyEngine as _AcqPE
            acquisition_enabled = _AcqPE().evaluate_acquisition_kill_switch()
            from src.models import CapabilityAcquisitionJob, AcquisitionCandidate
            _acq_rows = (
                db.query(CapabilityAcquisitionJob)
                .order_by(CapabilityAcquisitionJob.created_at.desc())
                .limit(30)
                .all()
            )
            for _row in _acq_rows:
                _cands = (
                    db.query(AcquisitionCandidate)
                    .filter(AcquisitionCandidate.job_id == _row.id)
                    .order_by(AcquisitionCandidate.created_at.desc())
                    .all()
                )
                acquisition_jobs.append({
                    "id": _row.id,
                    "job_id": _row.job_id,
                    "capability": _row.capability,
                    "state": _row.state,
                    "best_candidate_name": _row.best_candidate_name,
                    "best_candidate_version": _row.best_candidate_version,
                    "best_candidate_registry": _row.best_candidate_registry,
                    "governance_proposal_id": _row.governance_proposal_id,
                    "notes": _row.notes,
                    "created_at": _dt(_row.created_at),
                    "updated_at": _dt(_row.updated_at),
                    "candidates": [
                        {
                            "id": c.id,
                            "candidate_name": c.candidate_name,
                            "candidate_version": c.candidate_version,
                            "registry": c.registry,
                            "description": c.description,
                            "passed_sandbox": c.passed_sandbox,
                            "passed_governance": c.passed_governance,
                            "governance_proposal_id": c.governance_proposal_id,
                            "created_at": _dt(c.created_at),
                        }
                        for c in _cands
                    ],
                })
        except Exception as _acq_exc:
            logger.warning("Acquisition job load failed: %s", _acq_exc)

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

    # Swarm-health counters: silent-failure paths surfaced so operators can see
    # (and clear) them instead of having them grow forever in the background.
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

    # ── Structural routing stats ──────────────────────────────────────────────
    routing_stats: dict = {}
    try:
        from src.swarm.zero_token_router import get_routing_stats
        routing_stats = get_routing_stats().to_dict()
    except Exception as _rst_exc:
        logger.debug("routing_stats lookup failed: %s", _rst_exc)

    # ── Capability drift report (cached, ≤5 min TTL) ──────────────────────────
    drift_report: dict = {}
    try:
        from src.swarm.drift_detector import get_or_refresh as _drift_refresh
        import asyncio as _asyncio
        _dr = await _asyncio.get_event_loop().run_in_executor(None, _drift_refresh)
        drift_report = _dr.to_dict()
    except Exception as _dr_exc:
        logger.debug("drift_report lookup failed: %s", _dr_exc)

    return {
        "alerts": alerts, "open_alerts": open_alerts,
        "ledger": ledger, "metrics": metrics, "total_metrics": total_metrics,
        "cloud_shift_active": cloud_shift_active, "exec_locus_dist": exec_locus_dist,
        "failed_indexes": failed_indexes,
        "governance_proposal_failures": governance_proposal_failures,
        "metrics_reset_at": metrics_reset_at,
        "metrics_reset_by": metrics_reset_by,
        "proposals": proposals,
        "recent_proposals": recent_proposals,
        "open_proposals": open_proposals,
        "routing_stats": routing_stats,
        "drift_report": drift_report,
        "acquisition_jobs": acquisition_jobs,
        "acquisition_enabled": acquisition_enabled,
    }


@router.post("/governance/metrics/reset")
async def api_reset_swarm_metrics(metric: str = Form("all"), auth=Depends(require_auth)):
    """Zero a swarm-health failure counter (or all of them) from the dashboard.

    Auth-gated and audited, mirroring the alert-ack / cloud-shift controls. Once
    an operator has investigated a spike there is no other way to clear the
    cumulative counters, so an old incident stays visible forever otherwise.
    """
    from src.swarm.error_metrics import RESETTABLE_METRICS, reset_metric, reset_metrics
    operator = auth.get("username") or "operator"
    metric = (metric or "all").strip()
    if metric == "all":
        ok = reset_metrics(operator=operator)
    elif metric in RESETTABLE_METRICS:
        ok = reset_metric(metric, operator=operator)
    else:
        raise HTTPException(status_code=400, detail="Unknown counter.")
    if not ok:
        raise HTTPException(status_code=500, detail="Could not reset the counter(s).")

    try:
        from src.database import SessionLocal
        from src.models import AuditLog
        db = SessionLocal()
        try:
            db.add(AuditLog(
                user_id=auth.get("uid") or 0,
                command="swarm_metrics_reset",
                args=json.dumps({"metric": metric}),
                status="ok",
                output_preview="reset %s" % metric,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("swarm_metrics_reset audit log write failed", exc_info=True)

    return {"ok": True, "metric": metric}


@router.get("/governance/epistemic-trend")
async def api_epistemic_trend(
    window: str = "30",
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    _=Depends(require_auth),
):
    """Epistemic-label trend + windowed summary totals for the Governance page.

    ``window`` is a run-count ("10"/"30"/"100") or date-range ("24h"/"7d"/"30d")
    key; unknown keys fall back to the default window. ``from``/``to``
    (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM``) scope the trend to an explicit
    calendar range and take precedence over ``window`` when supplied. Both the
    per-run trend and the summary ratios recompute for the chosen window/range.
    """
    from src.database import SessionLocal
    from src.web.routes_governance import _epistemic_trend
    db = SessionLocal()
    try:
        trend = _epistemic_trend(db, window=window, date_from=date_from, date_to=date_to)
    finally:
        db.close()
    trend["points"] = [
        {**p, "created_at": _dt(p["created_at"])} for p in trend["points"]
    ]
    return trend


@router.post("/governance/alerts/{alert_id}/ack")
async def api_ack_alert(alert_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import SentinelEvent
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
    return {"ok": True}


@router.post("/governance/cloud-shift")
async def api_set_cloud_shift(
    enabled: str = Form(""),
    reason: str = Form(""),
    auth=Depends(require_auth),
):
    """Flip the cloud_shift kill switch from the dashboard (auth-gated + audited).

    Persists to the agents_home store when a DSN is configured, otherwise to a
    dashboard override the runtime reads. Disabling it drops every cloud
    provider so subsequent task runs fall through to local_degraded/refuse.
    """
    from src.swarm.jurisdiction import set_cloud_shift
    on = enabled.strip().lower() in ("1", "true", "yes", "on")
    reason = reason.strip()[:500]
    updated_by = auth.get("username") or "operator"
    try:
        result = set_cloud_shift(on, reason=reason, updated_by=updated_by)
    except Exception as e:
        logger.warning("cloud_shift toggle failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not update the cloud_shift kill switch.")

    effective = bool(result.get("effective"))
    try:
        from src.database import SessionLocal
        from src.models import AuditLog
        db = SessionLocal()
        try:
            db.add(AuditLog(
                user_id=auth.get("uid") or 0,
                command="cloud_shift_toggle",
                args=json.dumps({"requested": on, "reason": reason, "source": result.get("source")}),
                status="ok",
                output_preview="cloud_shift=%s" % effective,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("cloud_shift audit log write failed", exc_info=True)

    payload = {"ok": True, "cloud_shift_active": effective, "source": result.get("source")}
    if effective != on:
        payload["warning"] = (
            "An environment-level override is in force; the kill switch reads %s."
            % ("enabled" if effective else "disabled")
        )
    return payload


@router.get("/provenance")
async def api_provenance(request: Request, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ClaimProvenanceRecord
    q = (request.query_params.get("q") or "").strip()
    page = max(1, int(request.query_params.get("page", 1) or 1))
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
        records = query.order_by(ClaimProvenanceRecord.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        rows = [{
            "id": r.id, "claim_text": r.claim_text, "origin_role": r.origin_role,
            "origin_provider": r.origin_provider, "confidence": r.confidence,
            "verified": r.verified, "created_at": _dt(r.created_at),
        } for r in records]
    finally:
        db.close()
    return {"records": rows, "q": q, "page": page, "total_pages": max(1, (total + per_page - 1) // per_page), "total": total}


@router.get("/memory")
async def api_memory(request: Request, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import SwarmMemoryLayer
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
        layers = query.order_by(SwarmMemoryLayer.created_at.desc()).limit(100).all()
        layer_types = [r[0] for r in db.query(SwarmMemoryLayer.layer_type).distinct().all() if r[0]]
        total = db.query(SwarmMemoryLayer).count()
        rows = [{
            "id": l.id, "layer_type": l.layer_type, "topic": l.topic or "",
            "summary": l.summary or "", "content": l.content or "",
            "created_at": _dt(l.created_at),
        } for l in layers]
    finally:
        db.close()

    # Durable substrate status + recent audit trail (now DB-backed, so these
    # survive process restarts). Best-effort: never break the page if the
    # substrate is unavailable.
    substrate_status: dict = {}
    substrate_events: list = []
    try:
        from src.memory import get_substrate
        substrate = get_substrate()
        if substrate is not None:
            substrate_status = substrate.status()
            substrate_events = substrate.audit_events(limit=20)
    except Exception as e:
        logger.warning("api_memory substrate status failed: %s", e)

    # Durable long-term memory entries (memory_entries table) — server-side
    # search / category / adapter / date filtering with pagination so the page
    # never has to load the whole store into the browser.
    category = (request.query_params.get("category") or "").strip()
    adapter = (request.query_params.get("adapter") or "").strip()
    after_raw = (request.query_params.get("after") or "").strip()
    before_raw = (request.query_params.get("before") or "").strip()
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.query_params.get("per_page") or 25)
    except (TypeError, ValueError):
        per_page = 25
    per_page = max(1, min(200, per_page))

    after_ts = _date_to_epoch(after_raw, end_of_day=False)
    before_ts = _date_to_epoch(before_raw, end_of_day=True)

    entries_result: dict = {
        "entries": [], "total": 0, "page": page, "per_page": per_page,
        "total_pages": 1, "categories": [], "adapters": [],
    }
    try:
        from src.memory import db_store
        entries_result = db_store.search_entries(
            adapter=adapter or None,
            q=q or None,
            category=category or None,
            after_ts=after_ts,
            before_ts=before_ts,
            page=page,
            per_page=per_page,
        )
    except Exception as e:
        logger.warning("api_memory durable entries query failed: %s", e)

    entry_rows = [{
        "memory_id": e.memory_id,
        "adapter": getattr(e, "adapter", "") or "",
        "content": e.content,
        "category": e.category,
        "confidence": e.confidence,
        "source": e.source,
        "verified": bool(e.verified),
        "access_count": e.access_count,
        "created_at": _epoch_to_iso(e.created_at),
        "updated_at": _epoch_to_iso(e.updated_at),
    } for e in entries_result.get("entries", [])]

    return {
        "layers": rows, "layer_types": layer_types, "q": q, "layer": layer,
        "total": total,
        "substrate": substrate_status,
        "substrate_events": substrate_events,
        "entries": entry_rows,
        "entry_total": entries_result.get("total", 0),
        "entry_page": entries_result.get("page", page),
        "entry_per_page": entries_result.get("per_page", per_page),
        "entry_total_pages": entries_result.get("total_pages", 1),
        "entry_categories": entries_result.get("categories", []),
        "entry_adapters": entries_result.get("adapters", []),
        "category": category, "adapter": adapter,
        "after": after_raw, "before": before_raw,
    }


@router.post("/memory/sync")
async def api_memory_sync(request: Request, _=Depends(require_auth)):
    from src.memory import get_substrate
    substrate = get_substrate()
    if substrate is None:
        raise HTTPException(status_code=503, detail="Memory substrate unavailable")
    try:
        data = await request.json()
        direction = data.get("direction", "both")
        if direction not in ("both", "local→cloud", "cloud→local"):
            direction = "both"
        report = substrate.sync(direction=direction, min_confidence=0.5)
        return {"ok": True, "summary": report.summary()}
    except Exception as e:
        logger.error("Memory sync failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/consolidate")
async def api_memory_consolidate(_=Depends(require_auth)):
    from src.memory import get_substrate
    substrate = get_substrate()
    if substrate is None:
        raise HTTPException(status_code=503, detail="Memory substrate unavailable")
    try:
        promoted = substrate.consolidate(min_confidence=0.75)
        return {"ok": True, "promoted": promoted}
    except Exception as e:
        logger.error("Memory consolidation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system")
async def api_system(_=Depends(require_auth)):
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
    return {"results": results, "error": error}


# ---------------------------------------------------------------------------
# Local AI Models (sovereign / offline inference)
# ---------------------------------------------------------------------------

@router.get("/local-models")
async def api_local_models(_=Depends(require_auth)):
    from src.web.local_models_status import _is_local, _provider_status
    from src.providers.config import llm_provider_specs
    from src.providers.local_registry import load_local_models, registry_path

    specs = [s for s in llm_provider_specs() if _is_local(s)]
    providers = [await _provider_status(s) for s in specs]
    reachable_count = sum(1 for p in providers if p["reachable"])
    registry = load_local_models()
    p = registry_path()
    return {
        "providers": providers,
        "reachable_count": reachable_count,
        "total_count": len(providers),
        "registry": registry,
        "registry_path": str(p),
        "registry_exists": p.exists(),
    }


@router.post("/local-models/registry/add")
async def api_local_models_registry_add(
    model_id: str = Form(""),
    provider: str = Form(""),
    _=Depends(require_auth),
):
    from src.providers.local_registry import add_model

    try:
        entry = add_model(model_id, provider)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to add model to registry: %s", e)
        raise HTTPException(500, "Could not write to the registry.")
    return {"ok": True, "entry": entry}


@router.post("/local-models/registry/update")
async def api_local_models_registry_update(
    model_id: str = Form(""),
    provider: str = Form(""),
    new_model_id: str = Form(""),
    new_provider: str = Form(""),
    details: str = Form(""),
    _=Depends(require_auth),
):
    import json
    from src.providers.local_registry import update_model

    extra: dict = {}
    if details.strip():
        try:
            parsed = json.loads(details)
        except ValueError:
            raise HTTPException(400, "Details must be valid JSON.")
        if not isinstance(parsed, dict):
            raise HTTPException(400, "Details must be a JSON object of key/value pairs.")
        extra = parsed

    try:
        entry = update_model(model_id, provider, new_model_id, new_provider, extra)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to update model in registry: %s", e)
        raise HTTPException(500, "Could not write to the registry.")
    return {"ok": True, "entry": entry}


@router.post("/local-models/registry/remove")
async def api_local_models_registry_remove(
    model_id: str = Form(""),
    provider: str = Form(""),
    _=Depends(require_auth),
):
    from src.providers.local_registry import remove_model

    try:
        remove_model(model_id, provider)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to remove model from registry: %s", e)
        raise HTTPException(500, "Could not write to the registry.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Local model nodes — Mesh / localhost endpoint config + connectivity probe
# ---------------------------------------------------------------------------

@router.get("/local-nodes")
async def api_local_nodes(_=Depends(require_auth)):
    from src.web.local_nodes import node_settings
    return node_settings()


@router.post("/local-nodes")
async def api_local_nodes_update(request: Request, _=Depends(require_auth)):
    from src.web.local_nodes import update_node_settings
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Expected a JSON body.")
    settings = data.get("settings") if isinstance(data, dict) else None
    if not isinstance(settings, dict):
        raise HTTPException(400, "Expected a 'settings' object of name/value pairs.")
    try:
        applied = update_node_settings(settings)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Failed to update local node settings: %s", e)
        raise HTTPException(500, "Could not save local node settings.")
    return {"ok": True, "applied": applied}


@router.post("/local-nodes/test")
async def api_local_nodes_test(
    base_url: str = Form(""),
    timeout: str = Form(""),
    _=Depends(require_auth),
):
    from src.web.local_nodes import (
        DEFAULT_PROBE_TIMEOUT, probe_endpoint, validate_timeout, validate_url,
    )
    url = base_url.strip()
    if not url:
        raise HTTPException(400, "An endpoint URL is required to test.")
    try:
        validate_url(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    t = DEFAULT_PROBE_TIMEOUT
    if timeout.strip():
        try:
            t = validate_timeout(timeout.strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
    result = await probe_endpoint(url, t)
    return {"ok": True, "timeout": t, **result}


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

@router.get("/capabilities")
async def api_capabilities(_=Depends(require_auth)):
    from src.web.routes_capabilities import _context
    from fastapi import Request as _Req
    # Build a minimal fake request for _context
    ctx = _context(None)
    ctx.pop("request", None)
    return ctx


@router.post("/capabilities/toggle")
async def api_toggle_capability(
    capability: str = Form(""),
    enabled: str = Form(""),
    _=Depends(require_auth),
):
    from src.web.settings_store import set_setting
    from src.web.routes_capabilities import _TOGGLEABLE, _TRUTHY
    entry = _TOGGLEABLE.get(capability.strip())
    if not entry:
        raise HTTPException(400, "Unknown capability.")
    env_var, label = entry
    on = enabled.strip().lower() in _TRUTHY
    try:
        set_setting(env_var, "true" if on else "false")
    except Exception:
        raise HTTPException(500, "Could not update setting.")
    return {"ok": True, "enabled": on, "label": label}


@router.post("/capabilities/test/browser")
async def api_test_browser(request: Request, url: str = Form(""), _=Depends(require_auth)):
    from src.web.routes_capabilities import _broker
    broker = _broker()
    res = await broker.request("browser_open", {"url": url.strip(), "source": "capabilities-ui"})
    return {
        "allowed": res.decision.allowed, "reason": res.decision.reason,
        "output": res.output, "screenshot": res.screenshot_b64,
    }


@router.post("/capabilities/test/ssh")
async def api_test_ssh(request: Request, command: str = Form(""), _=Depends(require_auth)):
    from src.web.routes_capabilities import _broker
    broker = _broker()
    res = await broker.request("ssh_run", {"command": command.strip(), "source": "capabilities-ui"})
    return {"allowed": res.decision.allowed, "reason": res.decision.reason, "output": res.output}


@router.post("/capabilities/test/compute")
async def api_test_compute(
    request: Request, command: str = Form(""), session=Depends(require_auth)
):
    """Run a local container_compute (subprocess) command through the governed
    capability path: jurisdiction gate + command allow-list + audit trail.

    Demonstrates governance pillar #4 for a non-LLM capability — a disallowed
    command is rejected (and audited) before any subprocess is spawned.
    """
    import asyncio
    import shlex as _shlex
    from src.capabilities.resolver import (
        governed_call, CapabilityDenied, CapabilityNotAvailable,
    )
    raw = (command or "").strip()
    if not raw:
        return {"allowed": False, "reason": "A command is required.", "output": None}
    try:
        argv = _shlex.split(raw)
    except ValueError:
        return {"allowed": False, "reason": "Command could not be parsed safely.", "output": None}
    try:
        # Sync adapter method run off the event loop so the audit record
        # reflects the real outcome (governed_call audits after execution).
        result = await asyncio.to_thread(
            governed_call, "container_compute", "run_command", argv,
            actor=session, source="capabilities-ui",
        )
        out = result.get("stdout") or result.get("stderr") or ""
        return {"allowed": True, "reason": "Allowed by policy.", "output": out}
    except CapabilityDenied as exc:
        return {"allowed": False, "reason": str(exc), "output": None}
    except CapabilityNotAvailable as exc:
        return {"allowed": False, "reason": str(exc), "output": None}


@router.post("/capabilities/test/code")
async def api_test_code(request: Request, _=Depends(require_auth)):
    from src.web.routes_capabilities import _broker
    broker = _broker()
    res = await broker.request("ssh_fetch_code", {"source": "capabilities-ui"})
    return {"allowed": res.decision.allowed, "reason": res.decision.reason, "output": res.output}


@router.post("/capabilities/test/history")
async def api_test_history(request: Request, browser: str = Form("chrome"), _=Depends(require_auth)):
    from src.web.routes_capabilities import _broker
    broker = _broker()
    res = await broker.request("ssh_read_history", {"browser": browser, "limit": 20, "source": "capabilities-ui"})
    return {"allowed": res.decision.allowed, "reason": res.decision.reason, "output": res.output}


@router.post("/capabilities/test/receipts")
async def api_test_receipts(request: Request, _=Depends(require_auth)):
    from src.web.routes_capabilities import _broker
    broker = _broker()
    res = await broker.request("imap_read_receipts", {"limit": 10, "lookback_days": 365, "source": "capabilities-ui"})
    output = res.output
    if res.decision.allowed and output and output.startswith("["):
        try:
            from src.subscriptions.discovery import parse_receipts
            msgs = json.loads(output)
            cands = parse_receipts(output)
            names = ", ".join(sorted({c["name"] for c in cands if not c.get("unknown")})) or "none recognised"
            unknown = sum(1 for c in cands if c.get("unknown"))
            extra = (" Plus %d unrecognised billing sender(s) to review." % unknown) if unknown else ""
            output = "Read %d recent billing email(s). Recognised services: %s.%s" % (len(msgs), names, extra)
        except Exception:
            pass
    return {"allowed": res.decision.allowed, "reason": res.decision.reason, "output": output}


@router.post("/capabilities/email-config")
async def api_email_config(
    request: Request,
    host: str = Form(""),
    port: str = Form("993"),
    user: str = Form(""),
    password: str = Form(""),
    mailbox: str = Form("INBOX"),
    _=Depends(require_auth),
):
    host = host.strip()
    user = user.strip()
    if not host or not user:
        raise HTTPException(400, "Mail host and username are required.")
    from src.database import SessionLocal
    from src.web.routes_capabilities import _upsert_vault
    db = SessionLocal()
    try:
        _upsert_vault(db, "imap-host", "IMAP_HOST", host)
        _upsert_vault(db, "imap-user", "IMAP_USER", user)
        _upsert_vault(db, "imap-port", "IMAP_PORT", port.strip() or "993")
        _upsert_vault(db, "imap-mailbox", "IMAP_MAILBOX", mailbox.strip() or "INBOX")
        if password.strip():
            _upsert_vault(db, "imap-password", "IMAP_PASSWORD", password.strip())
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Could not save email config.")
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return {"ok": True, "msg": "Email bridge configuration saved to vault."}


@router.post("/capabilities/ssh-config")
async def api_ssh_config(
    request: Request,
    host: str = Form(""),
    port: str = Form("22"),
    user: str = Form(""),
    private_key: str = Form(""),
    password: str = Form(""),
    host_fingerprint: str = Form(""),
    _=Depends(require_auth),
):
    host = host.strip()
    user = user.strip()
    private_key = private_key.strip()
    password = password.strip()
    if not host or not user or not (private_key or password):
        raise HTTPException(400, "Host, user, and a key or password are required.")
    from src.database import SessionLocal
    from src.web.routes_capabilities import _upsert_vault
    db = SessionLocal()
    try:
        _upsert_vault(db, "ssh-host", "SSH_HOST", host)
        _upsert_vault(db, "ssh-user", "SSH_USER", user)
        _upsert_vault(db, "ssh-port", "SSH_PORT", port.strip() or "22")
        if private_key:
            _upsert_vault(db, "ssh-private-key", "SSH_PRIVATE_KEY", private_key)
        if password:
            _upsert_vault(db, "ssh-password", "SSH_PASSWORD", password)
        if host_fingerprint.strip():
            _upsert_vault(db, "ssh-host-fingerprint", "SSH_HOST_FINGERPRINT", host_fingerprint.strip())
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Could not save SSH config.")
    finally:
        db.close()
    from src.web.routes_keys import sync_keys_to_env
    sync_keys_to_env()
    return {"ok": True, "msg": "SSH bridge configuration saved to vault."}


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

@router.get("/subscriptions")
async def api_subscriptions(_=Depends(require_auth)):
    from src.web.routes_subscriptions import _context
    ctx = _context(None)
    ctx.pop("request", None)
    ctx.pop("result", None)
    # Serialize dates
    for s in ctx.get("subscriptions", []):
        if s.get("last_checked_at"):
            s["last_checked_at"] = _dt(s["last_checked_at"])
        if s.get("created_at"):
            s["created_at"] = _dt(s["created_at"])
    for a in ctx.get("audit", []):
        if a.get("created_at"):
            a["created_at"] = _dt(a["created_at"])
    return ctx


@router.post("/subscriptions/discover")
async def api_discover(request: Request, browser: str = Form("chrome"), _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_history
    from src.web.routes_subscriptions import _broker, _insert_candidates, _discover_result
    broker = _broker()
    candidates, message = await discover_from_history(broker, browser=browser, limit=200)
    added, updated = _insert_candidates(candidates)
    return _discover_result(message, candidates, added, updated)


@router.post("/subscriptions/discover/email")
async def api_discover_email(request: Request, _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_email
    from src.web.routes_subscriptions import _broker, _insert_candidates, _discover_result
    broker = _broker()
    candidates, message = await discover_from_email(broker, limit=50, lookback_days=365)
    added, updated = _insert_candidates(candidates)
    return _discover_result(message, candidates, added, updated)


@router.post("/subscriptions/add")
async def api_add_subscription(
    name: str = Form(""),
    domain: str = Form(""),
    login_url: str = Form(""),
    account_url: str = Form(""),
    login_username: str = Form(""),
    est_cost: str = Form(""),
    notes: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Subscription
    from src.subscriptions.catalog import match_host
    name = name.strip()
    if not name:
        raise HTTPException(400, "A name is required.")
    entry = match_host(domain.strip()) if domain.strip() else None
    db = SessionLocal()
    try:
        db.add(Subscription(
            name=name,
            domain=(domain.strip() or (entry["domains"][0] if entry else None)),
            login_url=(login_url.strip() or (entry["login_url"] if entry else None)),
            account_url=(account_url.strip() or (entry["account_url"] if entry else None)),
            login_username=(login_username.strip() or None),
            est_cost=(est_cost.strip() or (entry.get("est_cost") if entry else None)),
            notes=(notes.strip() or None),
            status="confirmed",
            source="manual",
        ))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "msg": "Subscription added."}


@router.post("/subscriptions/{sub_id}/status")
async def api_set_sub_status(sub_id: int, status: str = Form(""), _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Subscription, SubscriptionAction
    if status not in ("confirmed", "dismissed", "candidate"):
        raise HTTPException(400, "Invalid status.")
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if not sub:
            raise HTTPException(404, "Not found.")
        sub.status = status
        db.add(SubscriptionAction(subscription_id=sub.id, action="status_change", status="ok", detail="-> %s" % status))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "msg": "Status updated."}


@router.post("/subscriptions/{sub_id}/credentials")
async def api_set_sub_credentials(
    sub_id: int,
    login_url: str = Form(""),
    account_url: str = Form(""),
    login_username: str = Form(""),
    password: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import Subscription, SubscriptionAction, ApiKey
    from src.web.crypto import encrypt_value
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if not sub:
            raise HTTPException(404, "Not found.")
        if login_url.strip():
            sub.login_url = login_url.strip()
        if account_url.strip():
            sub.account_url = account_url.strip()
        sub.login_username = login_username.strip() or None
        if password:
            provider = "subscription:%s" % sub.id
            key = db.query(ApiKey).filter(ApiKey.provider == provider).first()
            if key:
                key.encrypted_key = encrypt_value(password)
                key.is_active = True
                key.last_rotated_at = datetime.utcnow()
            else:
                key = ApiKey(
                    provider=provider, label="%s login" % sub.name,
                    encrypted_key=encrypt_value(password), is_active=True,
                    category="Subscription logins", last_rotated_at=datetime.utcnow(),
                )
                db.add(key)
                db.flush()
            sub.credential_key_id = key.id
        db.add(SubscriptionAction(
            subscription_id=sub.id, action="credentials_saved", status="ok",
            detail="password %s" % ("updated" if password else "unchanged"),
        ))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "msg": "Credentials saved."}


@router.post("/subscriptions/{sub_id}/delete")
async def api_delete_subscription(sub_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import Subscription, SubscriptionAction, ApiKey
    db = SessionLocal()
    try:
        sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
        if sub:
            if sub.credential_key_id:
                key = db.query(ApiKey).filter(ApiKey.id == sub.credential_key_id).first()
                if key:
                    db.delete(key)
            db.query(SubscriptionAction).filter(SubscriptionAction.subscription_id == sub_id).delete()
            db.delete(sub)
            db.commit()
    finally:
        db.close()
    return {"ok": True, "msg": "Subscription removed."}


@router.post("/subscriptions/{sub_id}/open")
async def api_open_subscription(sub_id: int, _=Depends(require_auth)):
    from src.subscriptions import manager
    res = await manager.open_account(sub_id)
    return {"kind": "open", "ok": res.get("ok"), "message": res.get("message"),
            "screenshot": res.get("screenshot"), "sub_id": sub_id}


@router.post("/subscriptions/{sub_id}/cancel/request")
async def api_cancel_request(sub_id: int, _=Depends(require_auth)):
    from src.subscriptions import manager
    res = await manager.request_cancel(sub_id)
    return {"kind": "cancel_request", "ok": res.get("ok"), "message": res.get("message"),
            "screenshot": res.get("screenshot"), "pending": res.get("pending"), "sub_id": sub_id}


@router.post("/subscriptions/{sub_id}/cancel/confirm")
async def api_cancel_confirm(sub_id: int, confirm: str = Form(""), _=Depends(require_auth)):
    if confirm.strip().upper() != "CANCEL":
        return {"kind": "cancel_confirm", "ok": False, "sub_id": sub_id,
                "message": "Confirmation text did not match — type CANCEL to confirm."}
    from src.subscriptions import manager
    res = await manager.confirm_cancel(sub_id)
    return {"kind": "cancel_confirm", "ok": res.get("ok"), "message": res.get("message"),
            "screenshot": res.get("screenshot"), "sub_id": sub_id}


# ---------------------------------------------------------------------------
# Finance (QuickBooks + Plaid)
# ---------------------------------------------------------------------------

def _finance_txn(t, vendors, projects):
    v = vendors.get(t.vendor_id)
    p = projects.get(t.project_id)
    return {
        "id": t.id,
        "source": t.source,
        "external_id": t.external_id,
        "date": _dt(t.txn_date),
        "amount": t.amount,
        "currency": t.currency,
        "name": t.name,
        "memo": t.memo,
        "category_raw": t.category_raw,
        "pending": t.pending,
        "vendor": v.name if v else None,
        "vendor_id": t.vendor_id,
        "project_code": p.code if p else None,
        "project_id": t.project_id,
        "attribution_status": t.attribution_status,
        "attribution_confidence": t.attribution_confidence,
        "attribution_method": t.attribution_method,
    }


def _finance_run(r):
    return {
        "id": r.id, "source": r.source, "trigger": r.trigger, "status": r.status,
        "transactions_ingested": r.transactions_ingested,
        "entities_ingested": r.entities_ingested,
        "removed_count": r.removed_count, "attributed_count": r.attributed_count,
        "error": r.error, "started_at": _dt(r.started_at), "finished_at": _dt(r.finished_at),
    }


@router.get("/finance")
async def api_finance(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import (
        FinanceProject, FinanceVendor, VendorProjectRule, FinanceSyncRun,
        FinanceAuditLog, FinanceTransaction,
    )
    from src.finance.providers import provider_status
    from src.finance.budget import budget_vs_actuals
    from src.finance.writeback import list_writes
    from src.web.settings_store import get_setting

    db = SessionLocal()
    try:
        projects = db.query(FinanceProject).order_by(FinanceProject.code).all()
        rules = db.query(VendorProjectRule).all()
        vendors = db.query(FinanceVendor).order_by(FinanceVendor.name).limit(500).all()
        runs = db.query(FinanceSyncRun).order_by(FinanceSyncRun.id.desc()).limit(10).all()
        audit = db.query(FinanceAuditLog).order_by(FinanceAuditLog.id.desc()).limit(25).all()
        txn_count = db.query(FinanceTransaction).count()
        budget = budget_vs_actuals(db)
        writes = list_writes(db)
        return {
            "providers": provider_status(),
            "budget": budget,
            "projects": [{"id": p.id, "code": p.code, "name": p.name, "llc": p.llc,
                          "active": p.active} for p in projects],
            "rules": [{"id": r.id, "match_text": r.match_text, "project_id": r.project_id,
                       "note": r.note} for r in rules],
            "vendors": [{"id": v.id, "name": v.name, "source": v.source,
                         "default_project_id": v.default_project_id} for v in vendors],
            "recent_runs": [_finance_run(r) for r in runs],
            "audit": [{"id": a.id, "action": a.action, "status": a.status,
                       "detail": a.detail, "created_at": _dt(a.created_at)} for a in audit],
            "writes": writes,
            "txn_count": txn_count,
            "autosync_enabled": (get_setting("ENABLE_FINANCE_AUTOSYNC", "0") or "0") == "1",
        }
    finally:
        db.close()


@router.get("/finance/transactions")
async def api_finance_transactions(
    status: str = "", project_id: str = "", limit: str = "100",
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import FinanceTransaction, FinanceVendor, FinanceProject
    try:
        lim = max(1, min(int(limit or 100), 500))
    except ValueError:
        lim = 100
    db = SessionLocal()
    try:
        q = db.query(FinanceTransaction)
        if status.strip():
            q = q.filter(FinanceTransaction.attribution_status == status.strip())
        if project_id.strip().isdigit():
            q = q.filter(FinanceTransaction.project_id == int(project_id.strip()))
        rows = q.order_by(FinanceTransaction.txn_date.desc().nullslast(),
                          FinanceTransaction.id.desc()).limit(lim).all()
        vendors = {v.id: v for v in db.query(FinanceVendor).all()}
        projects = {p.id: p for p in db.query(FinanceProject).all()}
        return {"transactions": [_finance_txn(t, vendors, projects) for t in rows]}
    finally:
        db.close()


@router.post("/finance/sync")
async def api_finance_sync(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.finance.sync import sync_all
    db = SessionLocal()
    try:
        report = await asyncio.to_thread(sync_all, db, "manual")
    finally:
        db.close()
    if not report.get("ok"):
        raise HTTPException(status_code=400, detail=report.get("error") or "Sync failed.")
    return report


# -- Plaid Link (connect a bank securely) ---------------------------------- #
# These power the "Connect bank" flow: the browser asks for a short-lived
# link_token, opens Plaid Link, then posts the resulting public_token here for a
# SERVER-SIDE exchange. The long-lived access_token is encrypted at rest and is
# NEVER returned to the client.

def _plaid_csv_env(name, default):
    return [v.strip() for v in (os.environ.get(name) or default).split(",") if v.strip()]


def _plaid_audit(action, status, detail):
    """Write a single FinanceAuditLog row in its own session (secret-free detail).

    Every Plaid connection action is audited (governance pillar 4). ``detail`` must
    never carry a token/secret — only error codes/messages or item metadata."""
    from src.database import SessionLocal
    from src.models import FinanceAuditLog
    db = SessionLocal()
    try:
        db.add(FinanceAuditLog(action=action, status=status, source="finance-plaid",
                               detail=(str(detail)[:500] if detail else None)))
        db.commit()
    except Exception:  # noqa: BLE001 — auditing must never mask the real outcome
        db.rollback()
    finally:
        db.close()


@router.post("/finance/plaid/link-token")
async def api_finance_plaid_link_token(_=Depends(require_auth)):
    from src.finance.client_plaid import PlaidClient
    from src.finance.providers import (
        plaid_app_credentials, FinanceNotConfigured, FinanceAuthError, FinanceProviderError,
    )

    def _create():
        app = plaid_app_credentials()  # fail loud if client_id/secret missing
        client = PlaidClient(app_creds=app)
        redirect_uri = (os.environ.get("PLAID_REDIRECT_URI") or "").strip() or None
        return client.create_link_token(
            user_id="mydude-operator",  # stable client_user_id for this single-operator install
            products=_plaid_csv_env("PLAID_PRODUCTS", "transactions"),
            country_codes=[c.upper() for c in _plaid_csv_env("PLAID_COUNTRY_CODES", "US")],
            redirect_uri=redirect_uri,
        )

    try:
        result = await asyncio.to_thread(_create)
    except FinanceNotConfigured as e:
        await asyncio.to_thread(_plaid_audit, "plaid_link_token", "skipped", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except FinanceAuthError as e:
        await asyncio.to_thread(_plaid_audit, "plaid_link_token", "error", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except FinanceProviderError as e:
        await asyncio.to_thread(_plaid_audit, "plaid_link_token", "error", str(e))
        raise HTTPException(status_code=502, detail=str(e))
    await asyncio.to_thread(_plaid_audit, "plaid_link_token", "ok",
                            "Created a Plaid link token to start bank connection.")
    return {"link_token": result["link_token"], "expiration": result.get("expiration")}


@router.post("/finance/plaid/exchange")
async def api_finance_plaid_exchange(
    public_token: str = Form(...),
    institution_name: str = Form(""),
    institution_id: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.finance.client_plaid import PlaidClient
    from src.finance.providers import (
        plaid_app_credentials, save_plaid_item,
        FinanceNotConfigured, FinanceAuthError, FinanceProviderError,
    )
    from src.models import FinanceAuditLog

    tok = (public_token or "").strip()
    if not tok:
        raise HTTPException(status_code=400, detail="A Plaid public_token is required.")

    def _exchange_and_store():
        app = plaid_app_credentials()
        client = PlaidClient(app_creds=app)
        exchanged = client.exchange_public_token(tok)  # {access_token, item_id, ...}
        db = SessionLocal()
        try:
            row = save_plaid_item(
                db, item_id=exchanged["item_id"],
                access_token=exchanged["access_token"],
                institution_name=(institution_name or "").strip() or None,
                institution_id=(institution_id or "").strip() or None,
                source="link",
            )
            db.add(FinanceAuditLog(
                action="plaid_item_connected", status="ok", source="finance-plaid",
                detail="Linked Plaid item %s%s" % (
                    row.item_id,
                    " (%s)" % row.institution_name if row.institution_name else ""),
            ))
            db.commit()
            # NOTE: deliberately never returns the access_token.
            return {"id": row.id, "item_id": row.item_id,
                    "institution_name": row.institution_name,
                    "institution_id": row.institution_id, "status": row.status}
        finally:
            db.close()

    try:
        return await asyncio.to_thread(_exchange_and_store)
    except FinanceNotConfigured as e:
        await asyncio.to_thread(_plaid_audit, "plaid_item_connected", "skipped", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except FinanceAuthError as e:
        await asyncio.to_thread(_plaid_audit, "plaid_item_connected", "error", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except FinanceProviderError as e:
        await asyncio.to_thread(_plaid_audit, "plaid_item_connected", "error", str(e))
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/finance/plaid/items")
async def api_finance_plaid_items(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.finance.providers import list_plaid_items
    db = SessionLocal()
    try:
        items = [
            {**i, "last_synced_at": _dt(i["last_synced_at"]),
             "created_at": _dt(i["created_at"])}
            for i in list_plaid_items(db)
        ]
        return {"items": items}  # masked summaries — no access tokens
    finally:
        db.close()


@router.post("/finance/plaid/items/{item_pk}/remove")
async def api_finance_plaid_item_remove(item_pk: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.finance.client_plaid import PlaidClient
    from src.finance.providers import (
        plaid_app_credentials, delete_plaid_item,
        FinanceNotConfigured, FinanceAuthError, FinanceProviderError,
    )
    from src.models import PlaidItem, FinanceAuditLog
    from src.web.crypto import decrypt_value

    def _remove():
        db = SessionLocal()
        try:
            row = db.query(PlaidItem).filter(PlaidItem.id == item_pk).first()
            if row is None:
                raise HTTPException(status_code=404, detail="No linked bank with that id.")
            label = row.item_id
            inst = row.institution_name

            token = None
            try:
                token = decrypt_value(row.encrypted_access_token)
            except Exception:
                token = None

            if token is None:
                # Token unreadable (encryption key changed) — cannot revoke at
                # Plaid; remove the local record only, audited (not silent).
                delete_plaid_item(db, item_pk)
                db.add(FinanceAuditLog(
                    action="plaid_item_removed_local", status="warn", source="finance-plaid",
                    detail="Removed local Plaid item %s; token unreadable, Plaid "
                           "revoke not attempted." % label))
                db.commit()
                return {"removed": True, "revoked_at_plaid": False,
                        "note": "Token was unreadable; removed local record only."}

            app = plaid_app_credentials()
            client = PlaidClient(access_token=token, app_creds=app)
            try:
                client.item_remove()
            except FinanceAuthError as e:
                # Token already dead at Plaid (item gone / login required) — safe
                # to drop the local record; audit that revoke was unconfirmed.
                delete_plaid_item(db, item_pk)
                db.add(FinanceAuditLog(
                    action="plaid_item_removed_local", status="warn", source="finance-plaid",
                    detail="Removed local Plaid item %s; Plaid revoke not confirmed "
                           "(auth): %s" % (label, e)))
                db.commit()
                return {"removed": True, "revoked_at_plaid": False, "note": str(e)}
            except FinanceProviderError as e:
                # Transient/provider failure — keep the record, mark for retry.
                row.status = "error"
                row.last_error = str(e)[:500]
                db.add(FinanceAuditLog(
                    action="plaid_item_remove_failed", status="error", source="finance-plaid",
                    detail="Plaid revoke failed for %s: %s" % (label, e)))
                db.commit()
                raise HTTPException(status_code=502,
                                    detail="Could not revoke at Plaid: %s" % e)

            delete_plaid_item(db, item_pk)
            db.add(FinanceAuditLog(
                action="plaid_item_removed", status="ok", source="finance-plaid",
                detail="Disconnected Plaid item %s%s" % (
                    label, " (%s)" % inst if inst else "")))
            db.commit()
            return {"removed": True, "revoked_at_plaid": True}
        finally:
            db.close()

    try:
        return await asyncio.to_thread(_remove)
    except FinanceNotConfigured as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/finance/autosync")
async def api_finance_autosync(enabled: str = Form("false"), _=Depends(require_auth)):
    from src.web.settings_store import set_setting
    val = "1" if enabled.strip().lower() in ("1", "true", "yes", "on") else "0"
    set_setting("ENABLE_FINANCE_AUTOSYNC", val)
    return {"ok": True, "autosync_enabled": val == "1"}


@router.post("/finance/projects")
async def api_finance_create_project(
    code: str = Form(""), name: str = Form(""), llc: str = Form(""),
    description: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import FinanceProject
    code = code.strip()
    name = name.strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="Project code and name are required.")
    db = SessionLocal()
    try:
        if db.query(FinanceProject).filter(FinanceProject.code == code).first():
            raise HTTPException(status_code=400, detail="A project with that code already exists.")
        p = FinanceProject(code=code, name=name, llc=llc.strip() or None,
                           description=description.strip() or None)
        db.add(p)
        db.commit()
        db.refresh(p)
        return {"ok": True, "id": p.id, "code": p.code}
    finally:
        db.close()


@router.post("/finance/projects/{project_id}/budget")
async def api_finance_add_budget(
    project_id: int, amount: str = Form(""), category: str = Form(""),
    period: str = Form("total"), notes: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import FinanceProject, FinanceBudget
    try:
        amt = float(amount)
    except ValueError:
        raise HTTPException(status_code=400, detail="A numeric budget amount is required.")
    db = SessionLocal()
    try:
        if not db.query(FinanceProject).filter(FinanceProject.id == project_id).first():
            raise HTTPException(status_code=404, detail="Project not found.")
        b = FinanceBudget(project_id=project_id, amount=amt,
                          category=category.strip() or None,
                          period=(period.strip() or "total"), notes=notes.strip() or None)
        db.add(b)
        db.commit()
        return {"ok": True, "id": b.id}
    finally:
        db.close()


@router.post("/finance/transactions/{txn_id}/attribute")
async def api_finance_attribute(
    txn_id: int, project_id: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import FinanceTransaction, FinanceProject, FinanceAuditLog
    db = SessionLocal()
    try:
        t = db.query(FinanceTransaction).filter(FinanceTransaction.id == txn_id).first()
        if t is None:
            raise HTTPException(status_code=404, detail="Transaction not found.")
        if project_id.strip().isdigit():
            pid = int(project_id.strip())
            if not db.query(FinanceProject).filter(FinanceProject.id == pid).first():
                raise HTTPException(status_code=404, detail="Project not found.")
            t.project_id = pid
            t.attribution_status = "attributed"
            t.attribution_method = "manual"
            t.attribution_confidence = 1.0
        else:
            t.project_id = None
            t.attribution_status = "unattributed"
            t.attribution_method = "manual"
            t.attribution_confidence = 0.0
        db.add(FinanceAuditLog(action="manual_attribution", status="ok", source="finance",
                               detail="Transaction #%d -> project %s" % (txn_id, t.project_id)))
        db.commit()
        return {"ok": True, "project_id": t.project_id}
    finally:
        db.close()


@router.post("/finance/rules")
async def api_finance_create_rule(
    match_text: str = Form(""), project_id: str = Form(""), note: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import VendorProjectRule, FinanceProject
    match_text = match_text.strip()
    if not match_text or not project_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="Match text and a project are required.")
    pid = int(project_id.strip())
    db = SessionLocal()
    try:
        if not db.query(FinanceProject).filter(FinanceProject.id == pid).first():
            raise HTTPException(status_code=404, detail="Project not found.")
        rule = VendorProjectRule(match_text=match_text, project_id=pid, note=note.strip() or None)
        db.add(rule)
        db.commit()
        return {"ok": True, "id": rule.id}
    finally:
        db.close()


@router.post("/finance/vendors/{vendor_id}/default-project")
async def api_finance_vendor_default(
    vendor_id: int, project_id: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.models import FinanceVendor, FinanceProject
    db = SessionLocal()
    try:
        v = db.query(FinanceVendor).filter(FinanceVendor.id == vendor_id).first()
        if v is None:
            raise HTTPException(status_code=404, detail="Vendor not found.")
        if project_id.strip().isdigit():
            pid = int(project_id.strip())
            if not db.query(FinanceProject).filter(FinanceProject.id == pid).first():
                raise HTTPException(status_code=404, detail="Project not found.")
            v.default_project_id = pid
        else:
            v.default_project_id = None
        db.commit()
        return {"ok": True, "default_project_id": v.default_project_id}
    finally:
        db.close()


@router.get("/finance/writes")
async def api_finance_writes(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.finance.writeback import list_writes
    db = SessionLocal()
    try:
        return {"writes": list_writes(db)}
    finally:
        db.close()


@router.post("/finance/writes/request")
async def api_finance_write_request(
    kind: str = Form(""), target_external_id: str = Form(""),
    payload: str = Form(""), summary: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.finance.writeback import request_write
    try:
        payload_obj = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload must be valid JSON.")
    db = SessionLocal()
    try:
        try:
            res = request_write(db, kind.strip(), target_external_id.strip() or None,
                                payload_obj, summary.strip() or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "write": res}
    finally:
        db.close()


@router.post("/finance/writes/{request_id}/confirm")
async def api_finance_write_confirm(
    request_id: int, confirm: str = Form(""), _=Depends(require_auth),
):
    if confirm.strip().upper() != "CONFIRM":
        return {"ok": False, "message": "Confirmation text did not match — type CONFIRM to execute."}
    from src.database import SessionLocal
    from src.finance.writeback import confirm_write
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(confirm_write, db, request_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"ok": True, "write": res}
    finally:
        db.close()


@router.post("/finance/writes/{request_id}/reject")
async def api_finance_write_reject(request_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.finance.writeback import reject_write
    db = SessionLocal()
    try:
        try:
            res = reject_write(db, request_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "write": res}
    finally:
        db.close()


@router.post("/finance/suggestions/generate")
async def api_finance_suggestions_generate(_=Depends(require_auth)):
    """Generate auto-suggested categorize/create_bill drafts from live data.

    All drafts land in ``pending_confirm`` (behind the existing CONFIRM gate);
    nothing is posted to QuickBooks here. Runs in a worker thread because the
    engine makes blocking QuickBooks/IMAP calls. Fails loud (502) on a provider
    error; returns ``configured: false`` with an actionable message when
    QuickBooks is not connected.
    """
    from src.database import SessionLocal
    from src.finance.suggestions import generate_suggestions
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(generate_suggestions, db)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        return res
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Coach (PA / secretary + empathetic life-coach + mood sub-stack)
# ---------------------------------------------------------------------------

@router.get("/coach")
async def api_coach(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import CoachAuditLog
    from src.coach.providers import mood_provider_status
    from src.coach.delivery import delivery_status
    from src.coach.ingestion import recent_signals
    from src.coach.reflection import list_insights
    from src.coach.secretary import list_actions
    from src.web.settings_store import get_setting

    db = SessionLocal()
    try:
        audit = db.query(CoachAuditLog).order_by(CoachAuditLog.id.desc()).limit(25).all()
        return {
            "mood_provider": mood_provider_status(),
            "delivery": delivery_status(),
            "recent_signals": recent_signals(db, limit=50),
            "insights": list_insights(db, limit=50),
            "actions": list_actions(db, limit=50),
            "pending_actions": list_actions(db, limit=50, status="pending_confirm"),
            "audit": [{"id": a.id, "action": a.action, "status": a.status,
                       "source": a.source, "detail": a.detail,
                       "created_at": _dt(a.created_at)} for a in audit],
            "autoreflect_enabled": (get_setting("ENABLE_COACH_REFLECTION", "0") or "0") == "1",
            "strict_private": (get_setting("COACH_STRICT_PRIVATE", "0") or "0") == "1",
        }
    finally:
        db.close()


@router.get("/coach/signals")
async def api_coach_signals(
    signal_type: str = "", limit: str = "100", _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.coach.ingestion import recent_signals
    try:
        lim = max(1, min(int(limit or 100), 500))
    except ValueError:
        lim = 100
    db = SessionLocal()
    try:
        return {"signals": recent_signals(db, limit=lim,
                                          signal_type=signal_type.strip() or None)}
    finally:
        db.close()


@router.post("/coach/ingest")
async def api_coach_ingest(
    text: str = Form(""), prefer: str = Form("auto"),
    project_id: str = Form(""), event_ref: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.coach.ingestion import ingest_text
    from src.coach.providers import CoachNotConfigured, CoachProviderError, CoachAuthError
    from src.coach.llm import CoachLLMUnavailable
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text is required to capture a signal.")
    pid = int(project_id.strip()) if project_id.strip().isdigit() else None
    db = SessionLocal()
    try:
        try:
            sig = await asyncio.to_thread(
                ingest_text, db, text.strip(), prefer.strip() or "auto", None, pid,
                event_ref.strip() or None,
            )
        except CoachNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except CoachLLMUnavailable as e:
            raise HTTPException(status_code=400, detail=str(e))
        except (CoachAuthError, CoachProviderError) as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "signal": sig}
    finally:
        db.close()


@router.post("/coach/ingest-audio")
async def api_coach_ingest_audio(
    file: UploadFile = File(...), project_id: str = Form(""),
    event_ref: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.coach.ingestion import ingest_audio
    from src.coach.providers import CoachNotConfigured, CoachProviderError, CoachAuthError
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="An audio file is required to capture a voice signal.")
    pid = int(project_id.strip()) if project_id.strip().isdigit() else None
    db = SessionLocal()
    try:
        try:
            sig = await asyncio.to_thread(
                ingest_audio, db, audio, file.filename or "recording.webm", None,
                pid, event_ref.strip() or None,
            )
        except CoachNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except (CoachAuthError, CoachProviderError) as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "signal": sig}
    finally:
        db.close()


@router.post("/coach/behavior/compute")
async def api_coach_behavior(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.coach.behavior import compute_signals
    db = SessionLocal()
    try:
        return {"ok": True, **(await asyncio.to_thread(compute_signals, db, True))}
    finally:
        db.close()


@router.post("/coach/ask")
async def api_coach_ask(question: str = Form(""), _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.coach.coach import ask
    from src.coach.llm import CoachLLMUnavailable
    if not question.strip():
        raise HTTPException(status_code=400, detail="A question is required.")
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(ask, db, question.strip())
        except CoachLLMUnavailable as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, **res}
    finally:
        db.close()


@router.post("/coach/reflect")
async def api_coach_reflect(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.coach.reflection import run_reflection
    from src.coach.llm import CoachLLMUnavailable
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(run_reflection, db)
        except CoachLLMUnavailable as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, **res}
    finally:
        db.close()


@router.post("/coach/autoreflect")
async def api_coach_autoreflect(enabled: str = Form("false"), _=Depends(require_auth)):
    from src.web.settings_store import set_setting
    val = "1" if enabled.strip().lower() in ("1", "true", "yes", "on") else "0"
    set_setting("ENABLE_COACH_REFLECTION", val)
    return {"ok": True, "autoreflect_enabled": val == "1"}


@router.post("/coach/strict-private")
async def api_coach_strict_private(enabled: str = Form("false"), _=Depends(require_auth)):
    from src.web.settings_store import set_setting
    val = "1" if enabled.strip().lower() in ("1", "true", "yes", "on") else "0"
    set_setting("COACH_STRICT_PRIVATE", val)
    return {"ok": True, "strict_private": val == "1"}


@router.post("/coach/insights/{insight_id}/outcome")
async def api_coach_insight_outcome(
    insight_id: int, status: str = Form(""), outcome: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.coach.reflection import log_outcome
    db = SessionLocal()
    try:
        try:
            res = log_outcome(db, insight_id, status.strip(), outcome.strip() or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "insight": res}
    finally:
        db.close()


@router.get("/coach/actions")
async def api_coach_actions(status: str = "", _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.coach.secretary import list_actions
    db = SessionLocal()
    try:
        return {"actions": list_actions(db, limit=100, status=status.strip() or None)}
    finally:
        db.close()


@router.post("/coach/actions/request")
async def api_coach_action_request(
    kind: str = Form(""), recipient: str = Form(""), subject: str = Form(""),
    body: str = Form(""), payload: str = Form(""), summary: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.coach.secretary import request_action
    try:
        payload_obj = json.loads(payload) if payload.strip() else None
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload must be valid JSON.")
    db = SessionLocal()
    try:
        try:
            res = request_action(db, kind.strip(), recipient.strip() or None,
                                 subject.strip() or None, body or None, payload_obj,
                                 summary.strip() or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "action": res}
    finally:
        db.close()


@router.post("/coach/actions/{request_id}/confirm")
async def api_coach_action_confirm(
    request_id: int, confirm: str = Form(""), _=Depends(require_auth),
):
    if confirm.strip().upper() != "CONFIRM":
        return {"ok": False, "message": "Confirmation text did not match — type CONFIRM to execute."}
    from src.database import SessionLocal
    from src.coach.secretary import confirm_action
    from src.coach.delivery import DeliveryNotConfigured, DeliveryError
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(confirm_action, db, request_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except DeliveryNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except DeliveryError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "action": res}
    finally:
        db.close()


@router.post("/coach/actions/{request_id}/reject")
async def api_coach_action_reject(request_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.coach.secretary import reject_action
    db = SessionLocal()
    try:
        try:
            res = reject_action(db, request_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "action": res}
    finally:
        db.close()


@router.post("/coach/purge")
async def api_coach_purge(
    confirm: str = Form(""), ids: str = Form(""), _=Depends(require_auth),
):
    if confirm.strip().upper() != "PURGE":
        return {"ok": False, "message": "Confirmation text did not match — type PURGE to delete."}
    from src.database import SessionLocal
    from src.coach.ingestion import purge_signals
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()] if ids.strip() else None
    db = SessionLocal()
    try:
        return {"ok": True, **purge_signals(db, ids=id_list)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Avatar / persona / voice (Humanistic avatar layer — Azure-tier)
#
# Voice (ElevenLabs TTS) is Replit-native. Realistic real-time avatar video runs
# on the EXTERNAL GPU stack; here we only negotiate the session over HTTPS and hand
# the browser WebRTC connection info. Every live session enforces AI-use disclosure
# + recording consent before going active, with a voice-only fallback.
# ---------------------------------------------------------------------------


@router.get("/avatar")
async def api_avatar(_=Depends(require_auth)):
    """Aggregator: provider status, profiles, recent sessions, disclosure, audit."""
    from src.database import SessionLocal
    from src.models import AvatarAuditLog
    from src.avatar.voice import voice_status
    from src.avatar.bridge import avatar_status
    from src.avatar.profiles import list_profiles
    from src.avatar.sessions import list_sessions
    from src.avatar.compliance import disclosure_text, consent_prompt
    db = SessionLocal()
    try:
        audit = (db.query(AvatarAuditLog)
                 .order_by(AvatarAuditLog.id.desc()).limit(25).all())
        return {
            "voice": voice_status(),
            "avatar": avatar_status(),
            "profiles": list_profiles(db, limit=100),
            "sessions": list_sessions(db, limit=50),
            "disclosure": disclosure_text(),
            "consent_prompt": consent_prompt(),
            "audit": [
                {"action": a.action, "status": a.status, "detail": a.detail,
                 "created_at": a.created_at.isoformat() if a.created_at else None}
                for a in audit
            ],
        }
    finally:
        db.close()


@router.get("/avatar/voices")
async def api_avatar_voices(_=Depends(require_auth)):
    from src.avatar.voice import list_voices
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    try:
        voices = await asyncio.to_thread(list_voices)
    except AvatarNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AvatarAuthError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except AvatarProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"voices": voices}


@router.post("/avatar/voice/preview")
async def api_avatar_voice_preview(
    text: str = Form(""), voice_id: str = Form(""), _=Depends(require_auth),
):
    """Synthesize a short voice sample and return raw audio/mpeg (no base64)."""
    from src.avatar.voice import synthesize
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    text = (text or "").strip()
    voice_id = (voice_id or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required to synthesize a preview.")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Preview text is too long (max 500 characters).")
    if not voice_id:
        raise HTTPException(status_code=400, detail="A voice is required.")
    try:
        audio, content_type = await asyncio.to_thread(synthesize, text, voice_id)
    except AvatarNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AvatarAuthError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except AvatarProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=audio, media_type=content_type,
                    headers={"Cache-Control": "no-store"})


@router.get("/avatar/profiles")
async def api_avatar_profiles(_=Depends(require_auth)):
    from src.database import SessionLocal
    from src.avatar.profiles import list_profiles
    db = SessionLocal()
    try:
        return {"profiles": list_profiles(db, limit=100)}
    finally:
        db.close()


def _parse_avatar_config(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="avatar_config must be valid JSON.")


@router.post("/avatar/profiles")
async def api_avatar_profile_create(
    name: str = Form(""), persona: str = Form(""),
    voice_provider: str = Form("elevenlabs"), voice_id: str = Form(""),
    avatar_provider: str = Form(""), avatar_config: str = Form(""),
    disclosure_required: bool = Form(True), consent_required: bool = Form(True),
    bot_id: str = Form(""), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.avatar.profiles import create_profile
    config_obj = _parse_avatar_config(avatar_config)
    bot_id_val = int(bot_id) if bot_id.strip().isdigit() else None
    db = SessionLocal()
    try:
        try:
            res = create_profile(
                db, name.strip(), persona=persona.strip() or None,
                voice_provider=voice_provider.strip() or None,
                voice_id=voice_id.strip() or None,
                avatar_provider=avatar_provider.strip() or None,
                avatar_config=config_obj,
                disclosure_required=disclosure_required,
                consent_required=consent_required, bot_id=bot_id_val)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "profile": res}
    finally:
        db.close()


@router.patch("/avatar/profiles/{profile_id}")
async def api_avatar_profile_update(
    profile_id: int, name: str = Form(None), persona: str = Form(None),
    voice_provider: str = Form(None), voice_id: str = Form(None),
    avatar_provider: str = Form(None), avatar_config: str = Form(None),
    disclosure_required: bool = Form(None), consent_required: bool = Form(None),
    active: bool = Form(None), bot_id: str = Form(None), _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.avatar.profiles import update_profile
    fields = {}
    if name is not None:
        fields["name"] = name
    if persona is not None:
        fields["persona"] = persona
    if voice_provider is not None:
        fields["voice_provider"] = voice_provider.strip() or None
    if voice_id is not None:
        fields["voice_id"] = voice_id.strip() or None
    if avatar_provider is not None:
        fields["avatar_provider"] = avatar_provider.strip() or None
    if avatar_config is not None:
        fields["avatar_config"] = _parse_avatar_config(avatar_config)
    if disclosure_required is not None:
        fields["disclosure_required"] = disclosure_required
    if consent_required is not None:
        fields["consent_required"] = consent_required
    if active is not None:
        fields["active"] = active
    if bot_id is not None:
        fields["bot_id"] = int(bot_id) if bot_id.strip().isdigit() else None
    db = SessionLocal()
    try:
        try:
            res = update_profile(db, profile_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "profile": res}
    finally:
        db.close()


@router.delete("/avatar/profiles/{profile_id}")
async def api_avatar_profile_delete(profile_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.avatar.profiles import delete_profile
    db = SessionLocal()
    try:
        try:
            res = delete_profile(db, profile_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, **res}
    finally:
        db.close()


@router.get("/avatar/sessions")
async def api_avatar_sessions(status: str = "", _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.avatar.sessions import list_sessions
    db = SessionLocal()
    try:
        return {"sessions": list_sessions(db, limit=100, status=status.strip() or None)}
    finally:
        db.close()


@router.post("/avatar/session/start")
async def api_avatar_session_start(profile_id: int = Form(...), _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.avatar.sessions import start_session
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    from src.avatar.compliance import DisclosureRequired, ConsentRequired
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(start_session, db, profile_id)
        except AvatarNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except (DisclosureRequired, ConsentRequired) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except AvatarAuthError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except AvatarProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, **res}
    finally:
        db.close()


@router.post("/avatar/session/{session_id}/consent")
async def api_avatar_session_consent(
    session_id: int, granted: bool = Form(...), detail: str = Form(""),
    _=Depends(require_auth),
):
    from src.database import SessionLocal
    from src.avatar.sessions import record_consent
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    from src.avatar.compliance import DisclosureRequired, ConsentRequired
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(record_consent, db, session_id, granted,
                                          detail.strip() or None)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (DisclosureRequired, ConsentRequired) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except AvatarNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except AvatarAuthError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except AvatarProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "session": res}
    finally:
        db.close()


@router.post("/avatar/session/{session_id}/retry")
async def api_avatar_session_retry(session_id: int, _=Depends(require_auth)):
    """Re-attempt activation for a ``needs_provider`` session with granted consent.

    Lets the operator recover a session whose avatar backend errored during
    negotiation — without rolling back or re-collecting the consent the callee
    already gave. Returns the full connection descriptor in THIS response only.
    """
    from src.database import SessionLocal
    from src.avatar.sessions import retry_session
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    from src.avatar.compliance import DisclosureRequired, ConsentRequired
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(retry_session, db, session_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (DisclosureRequired, ConsentRequired) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except AvatarNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except AvatarAuthError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except AvatarProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "session": res}
    finally:
        db.close()


@router.post("/avatar/session/{session_id}/stream-start")
async def api_avatar_session_stream_start(session_id: int, _=Depends(require_auth)):
    """Begin provider media publishing for an active avatar_video session.

    The browser connects to the negotiated room first, then calls this so the
    avatar's tracks actually flow (e.g. HeyGen ``streaming.start``, which needs the
    server-side key). Returns a sanitized status — never the connection tokens.
    """
    from src.database import SessionLocal
    from src.avatar.sessions import start_stream
    from src.avatar.providers import (
        AvatarNotConfigured, AvatarAuthError, AvatarProviderError,
    )
    db = SessionLocal()
    try:
        try:
            res = await asyncio.to_thread(start_stream, db, session_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except AvatarNotConfigured as e:
            raise HTTPException(status_code=400, detail=str(e))
        except AvatarAuthError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except AvatarProviderError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "session": res}
    finally:
        db.close()


@router.post("/avatar/session/{session_id}/end")
async def api_avatar_session_end(session_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.avatar.sessions import end_session
    db = SessionLocal()
    try:
        try:
            res = end_session(db, session_id)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "session": res}
    finally:
        db.close()
