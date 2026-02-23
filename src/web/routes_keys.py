import os
import logging
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from src.database import SessionLocal
from src.models import ApiKey
from src.web.crypto import encrypt_value, decrypt_value, mask_key
from src.web.auth import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

PROVIDER_ENV_MAP = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "GROK_API_KEY",
}


def sync_keys_to_env():
    db = SessionLocal()
    try:
        keys = db.query(ApiKey).filter(ApiKey.is_active == True).all()
        for env_var in PROVIDER_ENV_MAP.values():
            if env_var in os.environ:
                del os.environ[env_var]
        for key in keys:
            env_var = PROVIDER_ENV_MAP.get(key.provider)
            if env_var:
                try:
                    os.environ[env_var] = decrypt_value(key.encrypted_key)
                except Exception as e:
                    logger.warning("Failed to decrypt key %s: %s", key.id, e)
    finally:
        db.close()


@router.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        keys = db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()
        key_list = []
        for k in keys:
            try:
                raw = decrypt_value(k.encrypted_key)
                masked = mask_key(raw)
            except Exception:
                masked = "••••••••(error)"
            key_list.append({
                "id": k.id,
                "provider": k.provider,
                "label": k.label or "",
                "masked_key": masked,
                "is_active": k.is_active,
                "created_at": k.created_at,
            })
    finally:
        db.close()
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    return templates.TemplateResponse("keys.html", {
        "request": request,
        "keys": key_list,
        "msg": msg,
        "err": err,
    })


@router.post("/keys")
async def add_key(
    request: Request,
    provider: str = Form(...),
    label: str = Form(""),
    api_key: str = Form(...),
    _=Depends(require_auth),
):
    db = SessionLocal()
    try:
        encrypted = encrypt_value(api_key.strip())
        new_key = ApiKey(
            provider=provider.lower().strip(),
            label=label.strip() or None,
            encrypted_key=encrypted,
            is_active=True,
        )
        db.add(new_key)
        db.commit()
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/keys?err=Failed to add key: {e}", status_code=303)
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=API key added successfully", status_code=303)


@router.post("/keys/{key_id}/toggle")
async def toggle_key(key_id: int, _=Depends(require_auth)):
    db = SessionLocal()
    try:
        key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
        if key:
            key.is_active = not key.is_active
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
            db.delete(key)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    sync_keys_to_env()
    return RedirectResponse(url="/keys?msg=Key deleted", status_code=303)
