import os
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse

from src.database import SessionLocal
from src.models import ApiKey, KeyAuditLog
from src.web.crypto import encrypt_value, decrypt_value, mask_key
from src.web.auth import require_auth
from src.web.service_catalog import (
    SERVICE_CATALOG,
    CATEGORIES,
    get_service,
    env_var_for,
    category_for,
)

logger = logging.getLogger(__name__)
router = APIRouter()
from src.web.templating import templates

# Fallback mapping for keys saved before env_var was stored per-key. Derived
# from env_1 (config/providers.toml) so provider secret names live in one place.
try:
    from src.providers.config import provider_env_map
    LEGACY_ENV_MAP = {k: v for k, v in provider_env_map().items() if v}
except Exception as _e:  # pragma: no cover - config should always be present
    logger.warning("Could not load provider env map from env_1: %s", _e)
    LEGACY_ENV_MAP = {}

EXPIRY_WARN_DAYS = 14


def _resolve_env_var(key):
    if key.env_var:
        return key.env_var
    mapped = env_var_for(key.provider)
    if mapped:
        return mapped
    return LEGACY_ENV_MAP.get(key.provider)


def _audit(db, action, key=None, detail=None, provider=None, label=None):
    try:
        entry = KeyAuditLog(
            api_key_id=key.id if key else None,
            provider=provider if provider is not None else (key.provider if key else None),
            label=label if label is not None else (key.label if key else None),
            action=action,
            detail=detail,
        )
        db.add(entry)
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)


def sync_keys_to_env():
    """Inject active key values into the environment under their env var and
    record that they were made available for use."""
    db = SessionLocal()
    try:
        all_keys = db.query(ApiKey).all()
        active_keys = [k for k in all_keys if k.is_active]
        # Clear env vars for EVERY known/managed key (active or not) so that a
        # key that was disabled or deleted does not leave its secret lingering
        # in the process environment.
        managed_vars = set(LEGACY_ENV_MAP.values())
        for k in all_keys:
            ev = _resolve_env_var(k)
            if ev:
                managed_vars.add(ev)
        for ev in managed_vars:
            os.environ.pop(ev, None)

        now = datetime.utcnow()
        for key in active_keys:
            ev = _resolve_env_var(key)
            if not ev:
                continue
            try:
                os.environ[ev] = decrypt_value(key.encrypted_key)
                key.last_used_at = now
            except Exception as e:
                logger.warning("Failed to decrypt key %s: %s", key.id, e)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("sync_keys_to_env failed: %s", e)
    finally:
        db.close()


def _reminders(keys):
    """Build expiry/rotation reminder list for active keys."""
    now = datetime.utcnow()
    reminders = []
    for k in keys:
        name = k.label or get_service(k.provider) and get_service(k.provider)["name"] or k.provider
        if k.expires_at:
            days = (k.expires_at - now).days
            if days < 0:
                reminders.append({"level": "danger", "text": "%s expired %d day(s) ago" % (name, -days)})
            elif days <= EXPIRY_WARN_DAYS:
                reminders.append({"level": "warn", "text": "%s expires in %d day(s)" % (name, days)})
        if k.rotation_days:
            base = k.last_rotated_at or k.created_at or now
            due = base + timedelta(days=k.rotation_days)
            overdue = (now - due).days
            if overdue >= 0:
                reminders.append({"level": "warn", "text": "%s is due for rotation" % name})
    return reminders


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, _=Depends(require_auth)):
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
            target = db.query(ApiKey).filter(ApiKey.id == reveal_id).first()
            if target:
                try:
                    revealed_value = decrypt_value(target.encrypted_key)
                    _audit(db, "reveal", key=target, detail="Key value revealed in UI")
                    db.commit()
                except Exception:
                    revealed_value = None
                    _audit(db, "reveal_failed", key=target, detail="Decryption failed during reveal")
                    db.commit()

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
                "expires_at": k.expires_at,
                "rotation_days": k.rotation_days,
                "last_used_at": k.last_used_at,
                "created_at": k.created_at,
            }
            if q:
                hay = " ".join([
                    entry["provider"], entry["name"], entry["label"],
                    entry["category"], entry["env_var"],
                ]).lower()
                if q not in hay:
                    continue
            if cat and entry["category"] != cat:
                continue
            key_list.append(entry)

        used_categories = sorted({(k.category or (get_service(k.provider)["category"] if get_service(k.provider) else "Other")) for k in all_keys})
    finally:
        db.close()

    response = templates.TemplateResponse("keys.html", {
        "request": request,
        "keys": key_list,
        "catalog": SERVICE_CATALOG,
        "categories": CATEGORIES,
        "used_categories": used_categories,
        "reminders": reminders,
        "q": request.query_params.get("q") or "",
        "active_category": cat,
        "total_count": len(all_keys),
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })
    if revealed_value is not None:
        # Avoid storing a page that contains a plaintext secret in any cache.
        response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/keys")
async def add_key(
    request: Request,
    provider: str = Form(...),
    label: str = Form(""),
    api_key: str = Form(...),
    category: str = Form(""),
    env_var: str = Form(""),
    notes: str = Form(""),
    expires_at: str = Form(""),
    rotation_days: str = Form(""),
    _=Depends(require_auth),
):
    provider = provider.lower().strip()
    db = SessionLocal()
    try:
        exp = None
        if expires_at.strip():
            try:
                exp = datetime.strptime(expires_at.strip(), "%Y-%m-%d")
            except ValueError:
                exp = None
        rot = None
        if rotation_days.strip():
            try:
                rot = int(rotation_days.strip())
            except ValueError:
                rot = None

        new_key = ApiKey(
            provider=provider,
            label=label.strip() or None,
            encrypted_key=encrypt_value(api_key.strip()),
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
        _audit(db, "create", key=new_key, detail="Key added to vault")
        db.commit()
    except Exception as e:
        db.rollback()
        return RedirectResponse(url="/keys?err=Failed to add key: %s" % e, status_code=303)
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=Key saved to vault", status_code=303)


@router.post("/keys/{key_id}/rotate")
async def rotate_key(key_id: int, api_key: str = Form(...), _=Depends(require_auth)):
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if key:
            key.encrypted_key = encrypt_value(api_key.strip())
            key.last_rotated_at = datetime.utcnow()
            _audit(db, "rotate", key=key, detail="Key value rotated")
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=Key rotated", status_code=303)


@router.post("/keys/{key_id}/toggle")
async def toggle_key(key_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if key:
            key.is_active = not key.is_active
            _audit(db, "enable" if key.is_active else "disable", key=key)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=Key status updated", status_code=303)


@router.post("/keys/{key_id}/delete")
async def delete_key(key_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if key:
            # Capture the env var before the row is gone so it can be cleared
            # from the process environment (sync re-adds it from any remaining
            # active key that shares the same env var).
            ev = _resolve_env_var(key)
            if ev:
                os.environ.pop(ev, None)
            _audit(db, "delete", provider=key.provider, label=key.label, detail="Key removed from vault")
            db.delete(key)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=Key deleted", status_code=303)


@router.get("/keys/audit", response_class=HTMLResponse)
async def audit_page(request: Request, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        logs = db.query(KeyAuditLog).order_by(KeyAuditLog.created_at.desc()).limit(200).all()
        entries = [{
            "provider": l.provider or "-",
            "label": l.label or "",
            "action": l.action,
            "detail": l.detail or "",
            "created_at": l.created_at,
        } for l in logs]
    finally:
        db.close()
    return templates.TemplateResponse("audit.html", {
        "request": request,
        "entries": entries,
    })
