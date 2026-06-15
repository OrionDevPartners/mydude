"""Regression tests for self-service account management.

A logged-in non-admin user can change their own email and password through
``/api/me/email`` and ``/api/me/password`` (added alongside the admin-only
``/api/users`` endpoints). These tests exercise the live mounted ``/api/*``
surface with a real signed session cookie bound to a throwaway DB user.

Key points locked in:
  * The dev-bypass identity (uid is None) cannot use these endpoints — they
    require a real backing account.  The suite therefore disables
    ``DEV_AUTH_BYPASS`` so the minted cookie is honoured on its own merits.
  * Changing the password requires the correct current password (403 on wrong),
    and the new password must meet the same >=8 char rule as admin-set ones.
  * Email update enforces uniqueness against other accounts (409 on clash).

Runnable two ways:
  * ``python tests/test_self_service_account.py``  (standalone; exits non-zero on failure)
  * ``pytest tests/test_self_service_account.py``   (test_* functions; no plugins needed)

Requires the dev Postgres DB (DATABASE_URL).
"""
import os
import sys
import uuid
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from src.web import auth
from src.web.app import app as real_app
from src.web.auth import hash_password, verify_password


@contextmanager
def _env(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _client() -> TestClient:
    return TestClient(real_app, raise_server_exceptions=False)


def _cookie_for(uid: int, username: str, is_admin: bool = False) -> dict:
    token = auth._serializer.dumps(
        {"authenticated": True, "uid": uid, "username": username, "is_admin": is_admin}
    )
    return {"session_token": token}


@contextmanager
def _temp_user(password: str = "initpass1", email: str | None = None):
    """Create a throwaway active user, yield it, and delete it afterwards."""
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    uname = "selftest_%s" % uuid.uuid4().hex[:10]
    try:
        u = User(
            username=uname,
            email=email,
            password_hash=hash_password(password),
            is_active=True,
            is_admin=False,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
        yield uid, uname
    finally:
        try:
            obj = db.query(User).filter(User.id == uid).first()
            if obj:
                db.delete(obj)
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _reload(uid: int):
    from src.database import SessionLocal
    from src.models import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        return (u.email, u.password_hash) if u else (None, None)
    finally:
        db.close()


def test_change_password_requires_correct_current():
    with _env(DEV_AUTH_BYPASS=None), _temp_user(password="initpass1") as (uid, uname):
        c = _client()
        ck = _cookie_for(uid, uname)
        # Wrong current password -> 403, hash unchanged.
        r = c.post("/api/me/password",
                   data={"current_password": "WRONG", "new_password": "brandnew123"},
                   cookies=ck)
        assert r.status_code == 403, (r.status_code, r.text)
        _, h = _reload(uid)
        assert verify_password("initpass1", h), "password must not change on bad current pw"

        # Correct current password -> 200, hash now matches the new password.
        r = c.post("/api/me/password",
                   data={"current_password": "initpass1", "new_password": "brandnew123"},
                   cookies=ck)
        assert r.status_code == 200, (r.status_code, r.text)
        _, h = _reload(uid)
        assert verify_password("brandnew123", h), "new password must take effect"
        assert not verify_password("initpass1", h)


def test_change_password_enforces_min_length():
    with _env(DEV_AUTH_BYPASS=None), _temp_user(password="initpass1") as (uid, uname):
        c = _client()
        ck = _cookie_for(uid, uname)
        r = c.post("/api/me/password",
                   data={"current_password": "initpass1", "new_password": "short"},
                   cookies=ck)
        assert r.status_code == 400, (r.status_code, r.text)
        _, h = _reload(uid)
        assert verify_password("initpass1", h), "password must not change when new pw too short"


def test_update_own_email():
    with _env(DEV_AUTH_BYPASS=None), _temp_user() as (uid, uname):
        c = _client()
        ck = _cookie_for(uid, uname)
        new_email = "self_%s@example.com" % uuid.uuid4().hex[:8]
        r = c.post("/api/me/email", data={"email": new_email}, cookies=ck)
        assert r.status_code == 200, (r.status_code, r.text)
        email, _ = _reload(uid)
        assert email == new_email
        # Clearing the email is allowed.
        r = c.post("/api/me/email", data={"email": ""}, cookies=ck)
        assert r.status_code == 200, (r.status_code, r.text)
        email, _ = _reload(uid)
        assert email is None


def test_update_email_rejects_duplicate():
    shared = "dup_%s@example.com" % uuid.uuid4().hex[:8]
    with _env(DEV_AUTH_BYPASS=None), _temp_user(email=shared) as (_uid1, _u1), \
            _temp_user() as (uid2, uname2):
        c = _client()
        ck = _cookie_for(uid2, uname2)
        r = c.post("/api/me/email", data={"email": shared}, cookies=ck)
        assert r.status_code == 409, (r.status_code, r.text)


def test_dev_bypass_cannot_use_self_service():
    # With the dev bypass on, require_auth passes but uid is None, so the
    # self-service endpoints must refuse (no real account behind the session).
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        c = _client()
        r = c.post("/api/me/password",
                   data={"current_password": "x", "new_password": "brandnew123"})
        assert r.status_code == 400, (r.status_code, r.text)
        r = c.post("/api/me/email", data={"email": "x@example.com"})
        assert r.status_code == 400, (r.status_code, r.text)


def test_unauthenticated_is_rejected():
    with _env(DEV_AUTH_BYPASS=None):
        c = _client()
        # Don't follow the redirect — require_auth issues a 303 -> /login which
        # the TestClient would otherwise resolve to the SPA index (200).
        r = c.post("/api/me/email", data={"email": "x@example.com"},
                   follow_redirects=False)
        assert r.status_code in (303, 401), (r.status_code, r.text)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", fn.__name__, "->", repr(e))
    sys.exit(1 if failed else 0)
