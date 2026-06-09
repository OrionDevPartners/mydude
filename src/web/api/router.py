"""JSON API router — all endpoints return JSON for the React SPA.

Every route mirrors an existing Jinja2 route but returns structured JSON instead
of HTML. Authentication uses the same cookie-session mechanism so the browser's
existing session_token cookie is honoured.
"""
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from src.web.auth import (
    ADMIN_PASSWORD,
    MAX_PASSWORD_LEN,
    SESSION_MAX_AGE,
    _dev_auth_bypass_enabled,
    _login_failures,
    _serializer,
    _set_session_cookie,
    client_ip,
    require_auth,
)
from src.web.branding import PRODUCT
from src.web.ratelimit import client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

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
async def api_login(request: Request, password: str = Form("")):
    import secrets as _s
    ip = client_ip(request)
    allowed, retry_after = _login_failures.peek(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in %d seconds." % retry_after,
        )
    if len(password) <= MAX_PASSWORD_LEN and _s.compare_digest(password, ADMIN_PASSWORD):
        _login_failures.reset(ip)
        token = _serializer.dumps({"authenticated": True})
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, token)
        return resp
    _login_failures.record(ip)
    raise HTTPException(status_code=401, detail="Invalid password")


@router.post("/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_token")
    return resp


@router.get("/me")
async def api_me(request: Request):
    if _dev_auth_bypass_enabled():
        return {"authenticated": True, "dev_bypass": True}
    from itsdangerous import BadSignature, SignatureExpired
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        if data.get("authenticated") is not True:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return {"authenticated": True}
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=401, detail="Session expired")


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
    return {
        "recent_tasks": [_parse_task(t) for t in recent],
        "has_keys": has_keys,
    }


@router.post("/tasks/run")
async def api_run_task(request: Request, prompt: str = Form(""), _=Depends(require_auth)):
    import time
    from src.database import SessionLocal
    from src.models import TaskRun, ApiKey
    from src.web.ratelimit import RateLimiter, ConcurrencyGuard

    MAX_PROMPT_LEN = 8000
    prompt = prompt.strip()
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
        result = await orchestrator.run(prompt)

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
async def api_keys(request: Request, _=Depends(require_auth)):
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
                        label=target.label, action="reveal", detail="Key value revealed in UI"
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

    resp = JSONResponse({
        "keys": key_list,
        "catalog": SERVICE_CATALOG,
        "categories": CATEGORIES,
        "used_categories": used_categories,
        "reminders": reminders,
        "total_count": len(all_keys),
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
    _=Depends(require_auth),
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
        db.add(KeyAuditLog(api_key_id=new_key.id, provider=new_key.provider, label=new_key.label, action="create", detail="Key added to vault"))
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
async def api_rotate_key(key_id: int, api_key: str = Form(""), _=Depends(require_auth)):
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
        db.add(KeyAuditLog(api_key_id=key.id, provider=key.provider, label=key.label, action="rotate", detail="Key value rotated"))
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
async def api_toggle_key(key_id: int, _=Depends(require_auth)):
    from src.database import SessionLocal
    from src.models import ApiKey, KeyAuditLog
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if not key:
            raise HTTPException(404, "Key not found.")
        key.is_active = not key.is_active
        db.add(KeyAuditLog(api_key_id=key.id, provider=key.provider, label=key.label,
                           action="enable" if key.is_active else "disable"))
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
async def api_delete_key(key_id: int, _=Depends(require_auth)):
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
        db.add(KeyAuditLog(provider=key.provider, label=key.label, action="delete", detail="Key removed from vault"))
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
            "id": a.id, "rule": a.rule, "severity": a.severity,
            "detail": a.detail or "", "acknowledged": a.acknowledged,
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
            names = ", ".join(sorted({c["name"] for c in cands})) or "none recognised"
            output = "Read %d recent billing email(s). Recognised services: %s." % (len(msgs), names)
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
    added = _insert_candidates(candidates)
    return _discover_result(message, candidates, added)


@router.post("/subscriptions/discover/email")
async def api_discover_email(request: Request, _=Depends(require_auth)):
    from src.subscriptions.discovery import discover_from_email
    from src.web.routes_subscriptions import _broker, _insert_candidates, _discover_result
    broker = _broker()
    candidates, message = await discover_from_email(broker, limit=50, lookback_days=365)
    added = _insert_candidates(candidates)
    return _discover_result(message, candidates, added)


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
