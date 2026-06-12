"""JSON API router — all endpoints return JSON for the React SPA.

Every route mirrors an existing Jinja2 route but returns structured JSON instead
of HTML. Authentication uses the same cookie-session mechanism so the browser's
existing session_token cookie is honoured.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile,
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
router.include_router(fleet_router)
router.include_router(prompts_router)
router.include_router(evolution_router)

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

    MAX_PROMPT_LEN = 8000
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
    task_run = TaskRun(prompt=prompt, status="running")
    try:
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        task_id = task_run.id
    except Exception as e:
        db.rollback()
        db.close()
        _run_guard.release()
        raise HTTPException(status_code=500, detail="Could not start the task. Please try again.")

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
        result = await orchestrator.run(prompt, domain=domain, team=team, task_run_id=task_id)

        elapsed_ms = int((time.time() - start_time) * 1000)
        result_text = json.dumps(result, indent=2, default=str)
        scores = {}
        if "COMPLIANCE_SCORES" in result:
            scores["compliance"] = result["COMPLIANCE_SCORES"]
        if "HALLUCINATION_RISK" in result:
            scores["hallucination_risk"] = result["HALLUCINATION_RISK"]
        if "JURISDICTION" in result:
            scores["jurisdiction"] = result["JURISDICTION"]
        task_run.result = result_text
        task_run.status = "completed"
        task_run.execution_time_ms = elapsed_ms
        task_run.provider_scores = json.dumps(scores) if scores else None
        db.commit()
    except Exception as e:
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

    return {"ok": True, "task_id": task_id}


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

    return {
        "alerts": alerts, "open_alerts": open_alerts,
        "ledger": ledger, "metrics": metrics, "total_metrics": total_metrics,
        "cloud_shift_active": cloud_shift_active, "exec_locus_dist": exec_locus_dist,
    }


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
    return {"layers": rows, "layer_types": layer_types, "q": q, "layer": layer, "total": total}


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
    from src.web.routes_local_models import _is_local, _provider_status
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
