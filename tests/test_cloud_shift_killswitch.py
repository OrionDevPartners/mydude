"""Tests for the operator cloud_shift kill switch (dashboard flip).

Covers the no-DSN path (the one this deployment actually uses): the dashboard
POST persists an override in ``app_settings`` that the runtime reads, the
governance badge reflects it immediately, and the resolution precedence holds.

Two layers are exercised:
  1. The ``src/swarm/jurisdiction`` module directly — set/get round-trip,
     precedence (operator override beats the static env default), and cache
     invalidation.
  2. The live ``/api`` surface — POST /api/governance/cloud-shift flips the
     switch and GET /api/governance reports the new state.

Auth: a valid signed session cookie is minted in-process with the app's own
serializer (ADMIN_PASSWORD blocks a real login in a harness). The TestClient is
built WITHOUT a context manager so the heavy startup lifespan never runs. The
DB-backed override uses the real dev DB (app_settings table).

Runnable two ways:
  * ``python tests/test_cloud_shift_killswitch.py``  (standalone; non-zero on fail)
  * ``pytest tests/test_cloud_shift_killswitch.py``   (test_* functions)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from src.web import auth
from src.web.app import app as real_app
from src.swarm import jurisdiction
from src.web.settings_store import get_setting
from src.database import SessionLocal
from src.models import AppSetting


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


def _auth_cookies() -> dict:
    # A uid-less ``{"authenticated": True}`` cookie is rejected by the upgraded
    # ``resolve_session`` (legacy shared-password session). Mint a dev-bypass
    # cookie instead: it authenticates as admin outside a deployment without a
    # live DB lookup, which is exactly the surface these /api checks need.
    return {"session_token": auth.make_dev_session_token()}


def _clear_override():
    """Remove the persisted override row and reset the in-process cache."""
    db = SessionLocal()
    try:
        db.query(AppSetting).filter(
            AppSetting.key == jurisdiction.CLOUD_SHIFT_OVERRIDE_KEY
        ).delete()
        db.commit()
    finally:
        db.close()
    jurisdiction.invalidate_cloud_shift_cache()


# --------------------------------------------------------------------------- #
# 1. Module-level round-trip + precedence (no DSN -> app_settings override)
# --------------------------------------------------------------------------- #

def test_set_cloud_shift_persists_and_runtime_reads_it():
    with _env(PG_AGENTS_HOME_DSN=None, CLOUD_SHIFT_ENABLED=None):
        _clear_override()
        try:
            res = jurisdiction.set_cloud_shift(False, reason="incident", updated_by="tester")
            assert res["source"] == "app_settings", res
            assert res["effective"] is False, res
            # Persisted where the runtime reads it.
            assert get_setting(jurisdiction.CLOUD_SHIFT_OVERRIDE_KEY) == "false"
            # A fresh read (cache busted by set) reflects the kill switch.
            jurisdiction.invalidate_cloud_shift_cache()
            assert jurisdiction.get_cloud_shift() is False

            res = jurisdiction.set_cloud_shift(True, updated_by="tester")
            assert res["effective"] is True, res
            jurisdiction.invalidate_cloud_shift_cache()
            assert jurisdiction.get_cloud_shift() is True
        finally:
            _clear_override()


def test_operator_override_beats_static_env_default():
    # CLOUD_SHIFT_ENABLED=true is the deploy default, but a deliberate operator
    # "disable" during an incident must win (otherwise the kill switch is a lie).
    with _env(PG_AGENTS_HOME_DSN=None, CLOUD_SHIFT_ENABLED="true"):
        _clear_override()
        try:
            jurisdiction.set_cloud_shift(False, updated_by="tester")
            jurisdiction.invalidate_cloud_shift_cache()
            assert jurisdiction.get_cloud_shift() is False
        finally:
            _clear_override()


def test_env_default_used_when_no_override():
    with _env(PG_AGENTS_HOME_DSN=None, CLOUD_SHIFT_ENABLED="false"):
        _clear_override()
        try:
            assert jurisdiction.get_cloud_shift() is False
        finally:
            _clear_override()
    with _env(PG_AGENTS_HOME_DSN=None, CLOUD_SHIFT_ENABLED=None):
        _clear_override()
        try:
            assert jurisdiction.get_cloud_shift() is True
        finally:
            _clear_override()


# --------------------------------------------------------------------------- #
# 2. Live /api surface — auth gate + flip + badge reflects new state
# --------------------------------------------------------------------------- #

def test_api_cloud_shift_requires_auth():
    # Disable the dev auth bypass so the real auth gate is exercised.
    with _env(DEV_AUTH_BYPASS=None):
        c = _client()
        # Do not follow the redirect — require_auth sends unauthenticated callers
        # to /login (303), which would otherwise resolve to the SPA shell (200).
        r = c.post("/api/governance/cloud-shift", data={"enabled": "false"}, follow_redirects=False)
        assert r.status_code in (303, 401, 403), r.status_code


def test_api_cloud_shift_flip_round_trip():
    with _env(PG_AGENTS_HOME_DSN=None, CLOUD_SHIFT_ENABLED=None):
        _clear_override()
        try:
            c = _client()
            cookies = _auth_cookies()

            r = c.post("/api/governance/cloud-shift", data={"enabled": "false"}, cookies=cookies)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["cloud_shift_active"] is False, body
            assert body["source"] == "app_settings", body

            g = c.get("/api/governance", cookies=cookies)
            assert g.status_code == 200, g.text
            assert g.json()["cloud_shift_active"] is False, g.json()

            r = c.post("/api/governance/cloud-shift", data={"enabled": "true"}, cookies=cookies)
            assert r.status_code == 200, r.text
            assert r.json()["cloud_shift_active"] is True, r.json()

            g = c.get("/api/governance", cookies=cookies)
            assert g.json()["cloud_shift_active"] is True, g.json()
        finally:
            _clear_override()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            failures += 1
            import traceback
            print("FAIL", fn.__name__, "->", e)
            traceback.print_exc()
    print("\n%d/%d passed" % (len(fns) - failures, len(fns)))
    return failures


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
