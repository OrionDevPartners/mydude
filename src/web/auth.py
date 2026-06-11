import os
import secrets
import logging

import bcrypt
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func

from src.web.ratelimit import RateLimiter, client_ip

logger = logging.getLogger(__name__)

router = APIRouter()
from src.web.templating import templates

_SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
_serializer = URLSafeTimedSerializer(_SESSION_SECRET)
# Retained only to seed the initial admin account (migration path off the old
# single shared password). It is NOT used to authenticate requests anymore.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
SESSION_MAX_AGE = 86400

# Bound the password form field so a malicious client cannot stream an
# unbounded body into the hashing path.
MAX_PASSWORD_LEN = 256
MAX_USERNAME_LEN = 80

# Login abuse protection. We mitigate brute force with a per-IP failed-attempt
# lockout. After LOGIN_MAX_FAILURES failed attempts within LOGIN_WINDOW seconds
# an IP is locked out until the window rolls off. Successful login clears it.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 300
_login_failures = RateLimiter(max_events=LOGIN_MAX_FAILURES, window_seconds=LOGIN_WINDOW)

# A precomputed hash compared against when no matching user is found, so the
# response time of a bad-username attempt resembles a bad-password attempt
# (reduces a username-enumeration timing oracle).
_DUMMY_HASH = bcrypt.hashpw(b"mydude-no-such-user", bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# Password hashing (bcrypt) — plaintext is never persisted.
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    # bcrypt only considers the first 72 bytes; truncate explicitly so longer
    # inputs hash deterministically instead of raising.
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def authenticate_user(db, username: str, password: str):
    """Return the matching active User for valid credentials, else None.

    Performs a constant-ish-time dummy hash when no user matches so a caller
    cannot distinguish "unknown user" from "wrong password" by timing.
    """
    from src.models import User
    username = (username or "").strip()
    user = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .first()
    )
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def seed_admin_user():
    """Migration path off the single shared password.

    On first boot (no users yet) create an ``admin`` account whose password is
    the existing ADMIN_PASSWORD secret, so an existing deployment keeps working
    — operators sign in as ``admin`` with the same password, then create their
    own accounts and revoke the shared one.
    """
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username="admin",
                email=None,
                password_hash=hash_password(ADMIN_PASSWORD),
                is_active=True,
                is_admin=True,
            )
            db.add(admin)
            db.commit()
            logger.warning(
                "Seeded initial admin account 'admin' from ADMIN_PASSWORD. "
                "Sign in, create individual accounts, then change this password."
            )
    except Exception as e:
        db.rollback()
        logger.error("Failed to seed initial admin account: %s", e)
    finally:
        db.close()


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


def _dev_bypass_identity() -> dict:
    return {
        "authenticated": True,
        "dev_bypass": True,
        "uid": None,
        "username": "dev-bypass",
        "is_admin": True,
    }


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


def make_session_token(user) -> str:
    return _serializer.dumps(
        {
            "authenticated": True,
            "uid": user.id,
            "username": user.username,
            "is_admin": bool(user.is_admin),
        }
    )


def resolve_session(request: Request):
    """Return the live identity dict for the request, or None if not authed.

    Re-validates the session against the database every request so a deleted or
    deactivated account loses access immediately (per-user revocation), and so a
    legacy shared-password cookie (no ``uid``) is rejected after the upgrade.
    """
    if _dev_auth_bypass_enabled():
        return _dev_bypass_identity()
    token = request.cookies.get("session_token")
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("authenticated") is not True:
        return None
    uid = data.get("uid")
    if uid is None:
        return None  # legacy shared-password session — force re-login
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first()
        if user is None or not user.is_active:
            return None
        return {
            "authenticated": True,
            "uid": user.id,
            "username": user.username,
            "is_admin": bool(user.is_admin),
        }
    finally:
        db.close()


def require_auth(request: Request):
    data = resolve_session(request)
    if data is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return data


def require_admin(request: Request):
    data = require_auth(request)
    if not data.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return data


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    from datetime import datetime
    from src.database import SessionLocal

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

    if len(password) <= MAX_PASSWORD_LEN and len(username) <= MAX_USERNAME_LEN:
        db = SessionLocal()
        try:
            user = authenticate_user(db, username, password)
            if user is not None:
                _login_failures.reset(ip)
                user.last_login_at = datetime.utcnow()
                db.commit()
                token = make_session_token(user)
                response = RedirectResponse(url="/", status_code=303)
                _set_session_cookie(response, token)
                return response
        finally:
            db.close()

    _login_failures.record(ip)
    logger.info("Failed login attempt from %s", ip)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password"},
        status_code=401,
    )


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response
