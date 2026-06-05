import os
import secrets
import logging
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from src.web.ratelimit import RateLimiter, client_ip

logger = logging.getLogger(__name__)

router = APIRouter()
from src.web.templating import templates

_SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
_serializer = URLSafeTimedSerializer(_SESSION_SECRET)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
SESSION_MAX_AGE = 86400

# Bound the password form field so a malicious client cannot stream an
# unbounded body into the equality check.
MAX_PASSWORD_LEN = 256

# Login abuse protection. The app uses a single shared admin password, which is
# inherently brute-forceable; we mitigate that with a per-IP failed-attempt
# lockout. After LOGIN_MAX_FAILURES failed attempts within LOGIN_WINDOW seconds
# an IP is locked out until the window rolls off. Successful login clears it.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 300
_login_failures = RateLimiter(max_events=LOGIN_MAX_FAILURES, window_seconds=LOGIN_WINDOW)


def _in_deployment() -> bool:
    return os.environ.get("REPLIT_DEPLOYMENT") == "1"


def _dev_auth_bypass_enabled() -> bool:
    """Allow skipping login ONLY in the development environment.

    Double-gated so it can never affect a published deployment:
    - DEV_AUTH_BYPASS must be truthy (set only in the development env scope), AND
    - the app must NOT be running inside a Replit deployment.
    """
    flag = os.environ.get("DEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes", "on")
    return flag and not _in_deployment()


def _set_session_cookie(response, token: str) -> None:
    """Set the session cookie with environment-appropriate flags.

    ``secure`` is enabled in a published deployment (always HTTPS) so the cookie
    is never sent over plaintext; it is left off in development where the
    preview may be served over a non-HTTPS internal hop.
    """
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        secure=_in_deployment(),
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )


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
    ip = client_ip(request)
    allowed, retry_after = _login_failures.peek(ip)
    if not allowed:
        logger.warning("Login lockout active for %s", ip)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Too many failed attempts. Try again in %d seconds." % retry_after,
            },
            status_code=429,
        )

    if len(password) <= MAX_PASSWORD_LEN and secrets.compare_digest(password, ADMIN_PASSWORD):
        _login_failures.reset(ip)
        token = _serializer.dumps({"authenticated": True})
        response = RedirectResponse(url="/", status_code=303)
        _set_session_cookie(response, token)
        return response

    _login_failures.record(ip)
    logger.info("Failed login attempt from %s", ip)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid password"},
        status_code=401,
    )


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response
