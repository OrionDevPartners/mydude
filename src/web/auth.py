import os
import secrets
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

router = APIRouter()
from src.web.templating import templates

_SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
_serializer = URLSafeTimedSerializer(_SESSION_SECRET)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
SESSION_MAX_AGE = 86400


def _dev_auth_bypass_enabled() -> bool:
    """Allow skipping login ONLY in the development environment.

    Double-gated so it can never affect a published deployment:
    - DEV_AUTH_BYPASS must be truthy (set only in the development env scope), AND
    - the app must NOT be running inside a Replit deployment.
    """
    in_deployment = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    flag = os.environ.get("DEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes", "on")
    return flag and not in_deployment


def require_auth(request: Request):
    if _dev_auth_bypass_enabled():
        return {"authenticated": True, "dev_bypass": True}
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        if data.get("authenticated") is not True:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return data


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        token = _serializer.dumps({"authenticated": True})
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("session_token", token, httponly=True, max_age=SESSION_MAX_AGE, samesite="lax")
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response
