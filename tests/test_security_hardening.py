"""Regression tests for the web app's security hardening.

These lock in the abuse/cost protections that were previously only verified by
hand (curl / ad-hoc TestClient checks). Without committed tests they can silently
regress, so this suite exercises each guard against the **live mounted surface**
— the `/api/*` router in ``src/web/app.py`` — which reuses the exact same shared
limiters and bound constants defined in ``src/web/auth.py``,
``src/web/routes_tasks.py`` and ``src/web/routes_keys.py``.

Covered:
  * Login lockout: 5 failed attempts -> 6th returns 429; bad / over-long
    password -> 401; a successful login resets the per-IP failure counter.
  * /tasks/run prompt bound (empty + over-length -> 400), per-IP rate limit
    (-> 429) and the global concurrency guard (-> 429 when saturated).
  * /keys add + rotate field validation (missing / over-long / malformed input
    -> 400) before anything reaches the DB or crypto layer.
  * Exception handlers preserve 3xx redirects (so ``require_auth``'s 303 ->
    /login survives) and never leak a stack trace / raw error text on 5xx.

Auth: ``ADMIN_PASSWORD`` blocks a real browser login in an e2e harness, so we
mint a valid signed session cookie in-process with the app's own serializer and
attach it to the TestClient (see ``_mint_session_cookie``). Most protections
short-circuit before the DB, and the dev environment sets ``DEV_AUTH_BYPASS=1``
(which makes ``require_auth`` pass), so the suite needs no live database. The
TestClient is built WITHOUT a context manager so the heavy startup lifespan
(DB init, SPA build, scheduler boot) never runs.

Runnable two ways:
  * ``python tests/test_security_hardening.py``  (standalone; exits non-zero on failure)
  * ``pytest tests/test_security_hardening.py``   (test_* functions; no plugins needed)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.web import auth, routes_tasks
from src.web.app import (
    app as real_app,
    _http_exception_handler,
    _validation_exception_handler,
    _unhandled_exception_handler,
)
from src.web.auth import ADMIN_PASSWORD, MAX_PASSWORD_LEN
from src.web.ratelimit import ConcurrencyGuard
from src.web.routes_tasks import MAX_PROMPT_LEN
from src.web.routes_keys import (
    MAX_PROVIDER_LEN,
    MAX_LABEL_LEN,
    MAX_API_KEY_LEN,
    MAX_NOTES_LEN,
    MAX_ROTATION_DAYS,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@contextmanager
def _env(**overrides):
    """Temporarily set/unset env vars, restoring the prior state afterwards."""
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


def _client(raise_server_exceptions: bool = False) -> TestClient:
    """A TestClient over the real app, NOT entered as a context manager so the
    startup lifespan never runs (no DB init / SPA build / scheduler boot)."""
    return TestClient(real_app, raise_server_exceptions=raise_server_exceptions)


def _mint_session_cookie() -> str:
    """Mint a valid signed session token using the app's own serializer."""
    return auth._serializer.dumps({"authenticated": True})


def _reset_login_failures():
    auth._login_failures._events.clear()


def _reset_run_limiter():
    routes_tasks._run_limiter._events.clear()


def _reset_run_guard():
    # Drain any leaked slots from a prior test.
    with routes_tasks._run_guard._lock:
        routes_tasks._run_guard._active = 0


# A throwaway prompt that is well within bounds and non-empty.
_OK_PROMPT = "summarize this"


# --------------------------------------------------------------------------- #
# 1. Login lockout (src/web/auth.py  ->  /api/login)
# --------------------------------------------------------------------------- #

def test_login_lockout_after_five_failures_returns_429():
    _reset_login_failures()
    ip = "203.0.113.10"
    c = _client()
    hdr = {"X-Forwarded-For": ip}
    # The first LOGIN_MAX_FAILURES wrong attempts each return 401 and are counted.
    for i in range(auth.LOGIN_MAX_FAILURES):
        r = c.post("/api/login", data={"password": "wrong-%d" % i}, headers=hdr)
        assert r.status_code == 401, (i, r.status_code, r.text)
    # The next attempt is locked out *before* the password is even checked.
    r = c.post("/api/login", data={"password": "wrong-again"}, headers=hdr)
    assert r.status_code == 429, r.status_code
    assert "Too many failed attempts" in r.text, r.text


def test_login_bad_password_returns_401():
    _reset_login_failures()
    c = _client()
    r = c.post(
        "/api/login",
        data={"password": "definitely-not-the-admin-password"},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )
    assert r.status_code == 401, r.status_code
    assert "Invalid password" in r.text, r.text


def test_login_overlong_password_returns_401_not_500():
    # An over-length password must be rejected by the bound check (the equality
    # comparison is skipped) — not crash and not be accepted.
    _reset_login_failures()
    c = _client()
    r = c.post(
        "/api/login",
        data={"password": "x" * (MAX_PASSWORD_LEN + 1)},
        headers={"X-Forwarded-For": "203.0.113.12"},
    )
    assert r.status_code == 401, r.status_code


def test_successful_login_resets_failure_counter():
    _reset_login_failures()
    ip = "203.0.113.13"
    c = _client()
    hdr = {"X-Forwarded-For": ip}
    # Burn one short of the lockout threshold.
    for i in range(auth.LOGIN_MAX_FAILURES - 1):
        assert c.post("/api/login", data={"password": "nope-%d" % i}, headers=hdr).status_code == 401
    # A correct login clears the counter and sets a session cookie.
    ok = c.post("/api/login", data={"password": ADMIN_PASSWORD}, headers=hdr)
    assert ok.status_code == 200, ok.status_code
    assert "session_token" in ok.cookies or "set-cookie" in {k.lower() for k in ok.headers}
    assert not auth._login_failures._events.get(ip), "failure counter not reset on success"
    # Because the counter was reset, the next wrong attempt is a plain 401, not 429.
    again = c.post("/api/login", data={"password": "wrong-now"}, headers=hdr)
    assert again.status_code == 401, again.status_code


# --------------------------------------------------------------------------- #
# 2. /tasks/run prompt bound + rate limit + concurrency guard
#    (src/web/routes_tasks.py  ->  /api/tasks/run)
# --------------------------------------------------------------------------- #

def test_run_empty_prompt_rejected():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        r = _client().post("/api/tasks/run", data={"prompt": "   "},
                           headers={"X-Forwarded-For": "198.51.100.1"})
    assert r.status_code == 400, r.status_code
    assert "enter a prompt" in r.text.lower(), r.text


def test_run_overlong_prompt_rejected():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        r = _client().post(
            "/api/tasks/run",
            data={"prompt": "a" * (MAX_PROMPT_LEN + 1)},
            headers={"X-Forwarded-For": "198.51.100.2"},
        )
    assert r.status_code == 400, r.status_code
    assert "too long" in r.text.lower(), r.text


def test_run_rate_limit_returns_429_when_window_full():
    _reset_run_limiter()
    ip = "198.51.100.3"
    # Saturate the per-IP window directly so the endpoint sees a full bucket.
    for _ in range(routes_tasks._run_limiter.max_events):
        allowed, _ra = routes_tasks._run_limiter.check(ip)
        assert allowed
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        r = _client().post("/api/tasks/run", data={"prompt": _OK_PROMPT},
                           headers={"X-Forwarded-For": ip})
    assert r.status_code == 429, r.status_code
    assert "rate limit" in r.text.lower(), r.text
    _reset_run_limiter()


def test_run_concurrency_guard_rejects_when_saturated():
    # Make the cheap pre-checks pass so control actually reaches the guard, then
    # saturate the guard and confirm the endpoint rejects (429) rather than
    # queueing behind in-flight work.
    _reset_run_limiter()
    _reset_run_guard()
    orig_has = routes_tasks._has_active_keys
    orig_avail = routes_tasks._llm_providers_available
    routes_tasks._has_active_keys = lambda: True
    routes_tasks._llm_providers_available = lambda: True
    try:
        # Fill every concurrency slot.
        for _ in range(routes_tasks._run_guard.max_concurrent):
            assert routes_tasks._run_guard.try_acquire()
        with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
            r = _client().post("/api/tasks/run", data={"prompt": _OK_PROMPT},
                               headers={"X-Forwarded-For": "198.51.100.4"})
        assert r.status_code == 429, r.status_code
        assert "busy" in r.text.lower(), r.text
    finally:
        routes_tasks._has_active_keys = orig_has
        routes_tasks._llm_providers_available = orig_avail
        _reset_run_guard()
        _reset_run_limiter()


def test_concurrency_guard_unit_behavior():
    # The guard admits up to max_concurrent, rejects beyond it, and frees a slot
    # on release.
    g = ConcurrencyGuard(max_concurrent=2)
    assert g.try_acquire() is True
    assert g.try_acquire() is True
    assert g.try_acquire() is False  # full -> reject, not queue
    g.release()
    assert g.try_acquire() is True
    # release() never drives the counter negative.
    g.release()
    g.release()
    g.release()
    assert g._active == 0


# --------------------------------------------------------------------------- #
# 3. /keys add + rotate field validation (src/web/routes_keys.py)
# --------------------------------------------------------------------------- #

def _post_keys(**form):
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        return _client().post("/api/keys", data=form)


def test_keys_add_requires_provider():
    r = _post_keys(provider="", api_key="sk-test")
    assert r.status_code == 400, r.status_code
    assert "provider" in r.text.lower(), r.text


def test_keys_add_rejects_overlong_provider():
    r = _post_keys(provider="p" * (MAX_PROVIDER_LEN + 1), api_key="sk-test")
    assert r.status_code == 400, r.status_code


def test_keys_add_requires_api_key():
    r = _post_keys(provider="openai", api_key="   ")
    assert r.status_code == 400, r.status_code
    assert "required" in r.text.lower(), r.text


def test_keys_add_rejects_overlong_api_key():
    r = _post_keys(provider="openai", api_key="k" * (MAX_API_KEY_LEN + 1))
    assert r.status_code == 400, r.status_code
    assert "too long" in r.text.lower(), r.text


def test_keys_add_rejects_overlong_label():
    r = _post_keys(provider="openai", api_key="sk-test", label="l" * (MAX_LABEL_LEN + 1))
    assert r.status_code == 400, r.status_code


def test_keys_add_rejects_overlong_notes():
    r = _post_keys(provider="openai", api_key="sk-test", notes="n" * (MAX_NOTES_LEN + 1))
    assert r.status_code == 400, r.status_code


def test_keys_add_rejects_bad_expiry_format():
    r = _post_keys(provider="openai", api_key="sk-test", expires_at="31-12-2026")
    assert r.status_code == 400, r.status_code
    assert "yyyy-mm-dd" in r.text.lower(), r.text


def test_keys_add_rejects_non_numeric_rotation_days():
    r = _post_keys(provider="openai", api_key="sk-test", rotation_days="soon")
    assert r.status_code == 400, r.status_code


def test_keys_add_rejects_out_of_range_rotation_days():
    r = _post_keys(provider="openai", api_key="sk-test", rotation_days=str(MAX_ROTATION_DAYS + 1))
    assert r.status_code == 400, r.status_code


def test_keys_rotate_requires_value():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        r = _client().post("/api/keys/1/rotate", data={"api_key": "   "})
    assert r.status_code == 400, r.status_code
    assert "required" in r.text.lower(), r.text


def test_keys_rotate_rejects_overlong_value():
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        r = _client().post("/api/keys/1/rotate", data={"api_key": "k" * (MAX_API_KEY_LEN + 1)})
    assert r.status_code == 400, r.status_code
    assert "too long" in r.text.lower(), r.text


# --------------------------------------------------------------------------- #
# 4. Exception handlers: preserve redirects, never leak internals
#    (src/web/app.py)
# --------------------------------------------------------------------------- #

def _handler_app() -> TestClient:
    """A throwaway app wired with the production exception handlers + routes that
    deliberately raise, so the handlers are exercised in isolation."""
    uapp = FastAPI()
    uapp.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    uapp.add_exception_handler(RequestValidationError, _validation_exception_handler)
    uapp.add_exception_handler(Exception, _unhandled_exception_handler)

    @uapp.get("/api/redir")
    async def _redir():
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    @uapp.get("/api/notfound")
    async def _notfound():
        raise HTTPException(status_code=404, detail="visible-not-found")

    @uapp.get("/api/server")
    async def _server():
        raise HTTPException(status_code=500, detail="LEAKY-INTERNAL-DETAIL-xyz")

    @uapp.get("/api/boom")
    async def _boom():
        raise RuntimeError("dsn=postgres://user:pw@host/db SECRET-TOKEN-abc")

    return TestClient(uapp, raise_server_exceptions=False)


def test_handler_preserves_303_redirect():
    r = _handler_app().get("/api/redir", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert r.headers.get("location") == "/login", r.headers


def test_handler_passes_through_client_error_detail():
    r = _handler_app().get("/api/notfound")
    assert r.status_code == 404, r.status_code
    assert "visible-not-found" in r.text, r.text


def test_handler_masks_5xx_http_exception_detail():
    r = _handler_app().get("/api/server")
    assert r.status_code == 500, r.status_code
    assert "LEAKY-INTERNAL-DETAIL" not in r.text, r.text
    assert "unexpected error" in r.text.lower(), r.text


def test_handler_masks_unhandled_exception_and_hides_trace():
    r = _handler_app().get("/api/boom")
    assert r.status_code == 500, r.status_code
    body = r.text
    assert "unexpected error" in body.lower(), body
    # No raw exception text, secret, or traceback frames leak to the client.
    assert "RuntimeError" not in body, body
    assert "SECRET-TOKEN" not in body, body
    assert "postgres://" not in body, body
    assert "Traceback" not in body, body


# --------------------------------------------------------------------------- #
# 5. require_auth end-to-end: redirect when anonymous, pass with minted cookie
# --------------------------------------------------------------------------- #

def test_protected_route_redirects_to_login_when_anonymous():
    # With the dev bypass OFF and no cookie, a protected route must 303 -> /login
    # (require_auth raises a 303 HTTPException that the handler turns into a
    # real redirect rather than an error page).
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
        r = _client().get("/api/dashboard", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert r.headers.get("location") == "/login", r.headers


def test_minted_session_cookie_authenticates():
    # The in-process minted cookie is accepted by require_auth (proves the
    # technique the rest of the suite relies on), with the dev bypass OFF.
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
        c = _client()
        c.cookies.set("session_token", _mint_session_cookie())
        r = c.get("/api/me")
    assert r.status_code == 200, r.status_code
    assert r.json().get("authenticated") is True, r.text


def test_anonymous_me_is_unauthorized():
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None):
        r = _client().get("/api/me")
    assert r.status_code == 401, r.status_code


# --------------------------------------------------------------------------- #
# Dev-bypass endpoints  (src/web/api/router.py  ->  /api/auth/dev-*)
# --------------------------------------------------------------------------- #
#
# Rules under test:
#   1. /auth/dev-info returns available:true when REPLIT_DEPLOYMENT is unset.
#   2. /auth/dev-info returns available:false when REPLIT_DEPLOYMENT=1.
#   3. /auth/dev-login grants a session cookie + 200 when not in a deployment.
#   4. /auth/dev-login returns 403 when REPLIT_DEPLOYMENT=1.
#   5. The session cookie issued by /auth/dev-login is accepted by /api/me
#      (resolve_session honours dev-bypass cookies outside deployments).
#   6. A dev-bypass cookie is REJECTED (401) when REPLIT_DEPLOYMENT=1.

def test_dev_info_available_outside_deployment():
    with _env(REPLIT_DEPLOYMENT=None):
        r = _client().get("/api/auth/dev-info")
    assert r.status_code == 200, r.text
    assert r.json().get("available") is True, r.json()


def test_dev_info_unavailable_in_deployment():
    with _env(REPLIT_DEPLOYMENT="1"):
        r = _client().get("/api/auth/dev-info")
    assert r.status_code == 200, r.text
    assert r.json().get("available") is False, r.json()


def test_dev_login_grants_session_outside_deployment():
    with _env(REPLIT_DEPLOYMENT=None):
        r = _client().post("/api/auth/dev-login")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True, body
    assert body.get("dev_bypass") is True, body
    assert "session_token" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}


def test_dev_login_blocked_in_deployment():
    with _env(REPLIT_DEPLOYMENT="1"):
        r = _client().post("/api/auth/dev-login")
    assert r.status_code == 403, (r.status_code, r.text)


def test_dev_bypass_cookie_accepted_by_me_outside_deployment():
    with _env(REPLIT_DEPLOYMENT=None):
        from src.web.auth import make_dev_session_token
        token = make_dev_session_token()
        c = _client()
        c.cookies.set("session_token", token)
        r = c.get("/api/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("authenticated") is True, body
    assert body.get("dev_bypass") is True, body


def test_dev_bypass_cookie_rejected_in_deployment():
    with _env(REPLIT_DEPLOYMENT="1", DEV_AUTH_BYPASS=None):
        from src.web.auth import make_dev_session_token
        token = make_dev_session_token()
        c = _client()
        c.cookies.set("session_token", token)
        r = c.get("/api/me")
    assert r.status_code == 401, (r.status_code, r.text)


# --------------------------------------------------------------------------- #
# Standalone runner (mirrors the other suites in tests/)
# --------------------------------------------------------------------------- #

def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
