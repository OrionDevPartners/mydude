---
name: Web security test harness
description: How to test the FastAPI app's auth/abuse guards in-process without DB, startup, or a real login.
---

# Testing the web app's security hardening hermetically

The login lockout, prompt/key input bounds, rate limit, concurrency guard and
exception handlers are all reachable through the **live `/api/*` router**
(`src/web/app.py` mounts only `api_router`; the Jinja routers are legacy/unmounted).
The api routes import the *same* shared limiter instances and bound constants from
`auth.py` / `routes_tasks.py` / `routes_keys.py`, so testing the api surface
exercises that logic too.

**Why these techniques (they are non-obvious):**
- `ADMIN_PASSWORD` is set in the env, so a browser/e2e login can't be driven.
  Mint a session cookie in-process: `auth._serializer.dumps({"authenticated": True})`
  then `client.cookies.set("session_token", token)`.
- Build the TestClient **without** the `with` context manager so the startup
  lifespan (DB init, SPA build, schedulers) never runs. Most guards short-circuit
  before any DB query, so the suite needs no live database.
- `DEV_AUTH_BYPASS=1` is set in the dev env — it makes `require_auth` pass. For
  the anonymous-redirect tests you must unset it (and `REPLIT_DEPLOYMENT`) per-test.
- The shared limiters are module-level singletons; reset them between tests
  (`auth._login_failures._events.clear()`, `routes_tasks._run_limiter._events.clear()`,
  drain `_run_guard._active`) or use distinct `X-Forwarded-For` IPs.
- To reach the concurrency guard via the endpoint you must monkeypatch
  `routes_tasks._has_active_keys`/`_llm_providers_available` to `True` (they run
  before the guard); otherwise the no-keys/no-provider check returns first.
- To unit-test the exception handlers, import `_http_exception_handler` /
  `_validation_exception_handler` / `_unhandled_exception_handler` from
  `src/web/app.py`, register them on a throwaway app with routes that raise.
  Use `/api/...` paths so `_error_response` returns JSON (a browser path returns
  the SPA `index.html` with 200 because `static/spa/index.html` exists).

**How to apply:** see `tests/test_security_hardening.py`. Tests follow the repo
convention: `test_*` functions plus a `_run_all()` + `__main__` so they run
standalone (`python tests/...`) since pytest is not installed in this env.
