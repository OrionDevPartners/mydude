"""DB-backed happy-path tests for the API credential vault.

The committed security suite (``test_security_hardening.py``) is hermetic and
only covers the *rejection* paths for the vault (missing / over-long / malformed
input rejected before anything reaches the DB or crypto layer). This suite
proves the **positive** path that actually touches PostgreSQL + Fernet — where a
silent data-integrity regression would hide:

  * POST /api/keys encrypts the value at rest (ciphertext != plaintext, but
    decrypts back to it) and exposes it under the resolved env var via
    ``sync_keys_to_env()``.
  * POST /api/keys/{id}/rotate replaces the stored ciphertext with the new
    value and advances ``last_rotated_at``.
  * POST /api/keys/{id}/toggle to inactive removes the env var.
  * POST /api/keys/{id}/delete removes the row and clears the env var.

These exercise the **live mounted surface** — the ``/api/*`` router in
``src/web/app.py`` — which is the path the React SPA actually calls.

Auth: ``require_auth`` rejects the plain ``{"authenticated": True}`` cookie
(no ``uid``), so we enable the development bypass (``DEV_AUTH_BYPASS=1`` with
``REPLIT_DEPLOYMENT`` unset) for the duration of each test. The TestClient is
built WITHOUT a context manager so the heavy startup lifespan never runs; the DB
is reached directly via ``SessionLocal``. Each test uses a unique provider +
env var so it is fully isolated and cleans up its own rows, making the suite
repeatable. Tests skip cleanly when no ``DATABASE_URL`` is configured.

Runnable two ways:
  * ``python tests/test_keys_lifecycle.py``  (standalone; exits non-zero on failure)
  * ``pytest tests/test_keys_lifecycle.py``   (test_* functions; no plugins needed)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from src.web.app import app as real_app


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


@contextmanager
def _auth_env():
    """Enable the dev auth bypass so ``require_auth`` passes in-process."""
    with _env(DEV_AUTH_BYPASS="1", REPLIT_DEPLOYMENT=None):
        yield


def _client() -> TestClient:
    """A TestClient over the real app, NOT entered as a context manager so the
    startup lifespan never runs (no DB init / SPA build / scheduler boot)."""
    return TestClient(real_app, raise_server_exceptions=False)


def _db_or_skip():
    """Return a live SessionLocal, or None if no DB is reachable (test skips)."""
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        from src.database import SessionLocal, init_db
        init_db()
        return SessionLocal
    except Exception:
        return None


# Unique per-process identifiers so the test owns its rows and env var and is
# safe to run repeatedly / alongside real vault data.
_PROVIDER = "lifecycle-test-%d" % os.getpid()
_ENV_VAR = "MYDUDE_LIFECYCLE_TEST_%d" % os.getpid()
_KEY_VALUE = "sk-live-original-secret-value-123456"
_ROTATED_VALUE = "sk-live-rotated-secret-value-987654"


def _cleanup(SessionLocal):
    """Remove any rows + env var this test owns, regardless of where it failed."""
    from src.models import ApiKey, KeyAuditLog
    os.environ.pop(_ENV_VAR, None)
    db = SessionLocal()
    try:
        rows = db.query(ApiKey).filter(ApiKey.provider == _PROVIDER).all()
        for r in rows:
            db.query(KeyAuditLog).filter(KeyAuditLog.api_key_id == r.id).delete()
            db.delete(r)
        db.query(KeyAuditLog).filter(KeyAuditLog.provider == _PROVIDER).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    # Drop the env var from the vault sync tracking set so it isn't carried
    # across tests in the same process.
    try:
        from src.web import routes_keys
        routes_keys._vault_injected_vars.discard(_ENV_VAR)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_add_key_encrypts_at_rest_and_syncs_env():
    SessionLocal = _db_or_skip()
    if SessionLocal is None:
        print("   (skipped: no DATABASE_URL)")
        return
    from src.models import ApiKey
    from src.web.crypto import decrypt_value

    _cleanup(SessionLocal)
    try:
        with _auth_env():
            c = _client()
            r = c.post("/api/keys", data={
                "provider": _PROVIDER,
                "label": "Lifecycle Test Key",
                "api_key": _KEY_VALUE,
                "env_var": _ENV_VAR,
            })
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.json().get("ok") is True, r.text

        db = SessionLocal()
        try:
            row = db.query(ApiKey).filter(ApiKey.provider == _PROVIDER).one()
            # Persisted and active.
            assert row.is_active is True, row.is_active
            assert row.env_var == _ENV_VAR, row.env_var
            assert row.last_rotated_at is not None
            # Encrypted at rest: the stored value is NOT the plaintext, but it
            # decrypts back to exactly what we submitted.
            assert row.encrypted_key != _KEY_VALUE, "value stored in plaintext!"
            assert decrypt_value(row.encrypted_key) == _KEY_VALUE
        finally:
            db.close()

        # sync_keys_to_env() (called by the endpoint) exposed it under env_var.
        assert os.environ.get(_ENV_VAR) == _KEY_VALUE, os.environ.get(_ENV_VAR)
    finally:
        _cleanup(SessionLocal)


def test_rotate_changes_ciphertext_and_bumps_last_rotated():
    SessionLocal = _db_or_skip()
    if SessionLocal is None:
        print("   (skipped: no DATABASE_URL)")
        return
    from src.models import ApiKey
    from src.web.crypto import decrypt_value

    _cleanup(SessionLocal)
    try:
        with _auth_env():
            c = _client()
            r = c.post("/api/keys", data={
                "provider": _PROVIDER,
                "api_key": _KEY_VALUE,
                "env_var": _ENV_VAR,
            })
            assert r.status_code == 200, r.text

            db = SessionLocal()
            try:
                row = db.query(ApiKey).filter(ApiKey.provider == _PROVIDER).one()
                key_id = row.id
                old_cipher = row.encrypted_key
                old_rotated = row.last_rotated_at
            finally:
                db.close()

            r = c.post("/api/keys/%d/rotate" % key_id, data={"api_key": _ROTATED_VALUE})
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.json().get("ok") is True, r.text

        db = SessionLocal()
        try:
            row = db.query(ApiKey).filter(ApiKey.id == key_id).one()
            # Ciphertext replaced and decrypts to the NEW value.
            assert row.encrypted_key != old_cipher, "ciphertext unchanged after rotate"
            assert decrypt_value(row.encrypted_key) == _ROTATED_VALUE
            # last_rotated_at advanced.
            assert row.last_rotated_at is not None
            assert row.last_rotated_at > old_rotated, (old_rotated, row.last_rotated_at)
        finally:
            db.close()

        # The newly rotated value is what is now exposed to the environment.
        assert os.environ.get(_ENV_VAR) == _ROTATED_VALUE, os.environ.get(_ENV_VAR)
    finally:
        _cleanup(SessionLocal)


def test_toggle_inactive_removes_env_then_delete_clears_row():
    SessionLocal = _db_or_skip()
    if SessionLocal is None:
        print("   (skipped: no DATABASE_URL)")
        return
    from src.models import ApiKey

    _cleanup(SessionLocal)
    try:
        with _auth_env():
            c = _client()
            r = c.post("/api/keys", data={
                "provider": _PROVIDER,
                "api_key": _KEY_VALUE,
                "env_var": _ENV_VAR,
            })
            assert r.status_code == 200, r.text
            assert os.environ.get(_ENV_VAR) == _KEY_VALUE

            db = SessionLocal()
            try:
                key_id = db.query(ApiKey).filter(ApiKey.provider == _PROVIDER).one().id
            finally:
                db.close()

            # Toggle inactive: the secret must not linger in the environment.
            r = c.post("/api/keys/%d/toggle" % key_id)
            assert r.status_code == 200, r.text
            assert r.json().get("is_active") is False, r.text
        assert os.environ.get(_ENV_VAR) is None, "env var lingered after disable"

        db = SessionLocal()
        try:
            row = db.query(ApiKey).filter(ApiKey.id == key_id).one()
            assert row.is_active is False, row.is_active
        finally:
            db.close()

        # Delete: row gone and env var stays cleared.
        with _auth_env():
            r = c.post("/api/keys/%d/delete" % key_id)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True, r.text

        db = SessionLocal()
        try:
            assert db.query(ApiKey).filter(ApiKey.id == key_id).first() is None, \
                "row not removed after delete"
        finally:
            db.close()
        assert os.environ.get(_ENV_VAR) is None, "env var lingered after delete"
    finally:
        _cleanup(SessionLocal)


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
