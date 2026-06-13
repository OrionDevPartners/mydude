"""Tests for the Local AI Models *write* endpoints the dashboard buttons hit.

The React SPA's Local AI Models panel adds and removes registry entries by
POSTing to the JSON twins at ``/api/local-models/registry/add`` and
``/api/local-models/registry/remove`` (src/web/api/router.py,
``api_local_models_registry_add`` / ``api_local_models_registry_remove``).

The underlying writer functions (src/providers/local_registry.py, add_model /
remove_model) have their own coverage in tests/test_local_registry_writes.py.
This suite locks in the *endpoint* contract the SPA actually depends on so a
regression in request handling, the ValueError -> 400 / other -> 500 error
mapping, or the ``{"ok": true, "entry": ...}`` response shape can't slip
through unnoticed. (The /registry/update endpoint is covered separately; this
suite is scoped to add + remove only.)

Covered:
  * add returns 200 with ``{"ok": true, "entry": {model_id, provider}}`` and
    GET /api/local-models then shows the new entry in its registry array.
  * a duplicate add, and blank / oversized input, return 400.
  * remove returns 200 (``{"ok": true}``) and the entry disappears from
    GET /api/local-models; removing a non-existent entry returns 400.
  * with DEV_AUTH_BYPASS off (and not in a deployment) both write endpoints
    refuse to serve — require_auth raises its 303 -> /login redirect.

We mount the real ``/api`` router (src/web/api/router.py) on a throwaway
FastAPI app — exactly like tests/test_api_local_models.py — so the TestClient
exercises the same handler the SPA hits. Authentication is satisfied via
DEV_AUTH_BYPASS so no login round-trip or cookie is needed.
LOCAL_MODEL_REGISTRY_PATH points at a throwaway temp file so nothing touches
the real ~/.mydude registry. No local server, secret, or network is required.

Runnable two ways:
  * ``python tests/test_api_local_models_writes.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_api_local_models_writes.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api.router import router as api_router

MAX_MODEL_ID_LEN = 256
MAX_PROVIDER_LEN = 64


def _client() -> TestClient:
    """A TestClient over a minimal app that mounts only the real /api router."""
    app = FastAPI()
    app.include_router(api_router)
    # Don't raise server exceptions so a require_auth 303 surfaces as a response.
    return TestClient(app, raise_server_exceptions=False)


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
def _registry(initial_contents=None):
    """Point LOCAL_MODEL_REGISTRY_PATH at a fresh temp registry (bypass on).

    When ``initial_contents`` is None the file is left absent so add_model
    exercises its create-on-missing path. Yields the resolved path.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model_registry.yaml")
        if initial_contents is not None:
            with open(path, "w") as f:
                f.write(initial_contents)
        with _env(LOCAL_MODEL_REGISTRY_PATH=path, DEV_AUTH_BYPASS="1",
                  REPLIT_DEPLOYMENT=None):
            yield path


def _registry_ids(client) -> set:
    """The set of model_ids GET /api/local-models currently reports."""
    body = client.get("/api/local-models").json()
    return {m["model_id"] for m in body["registry"]}


# -- add: happy path ----------------------------------------------------------

def test_add_returns_ok_and_entry_and_shows_in_listing():
    with _registry():
        client = _client()
        resp = client.post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, body
        assert body["entry"] == {"model_id": "llama3.2:3b", "provider": "ollama"}, body

        # And GET /api/local-models now surfaces it in the registry array.
        listing = client.get("/api/local-models").json()
        assert listing["registry_exists"] is True, listing
        by_id = {m["model_id"]: m for m in listing["registry"]}
        assert "llama3.2:3b" in by_id, listing
        assert by_id["llama3.2:3b"]["provider"] == "ollama", listing


def test_add_trims_whitespace_in_entry():
    with _registry():
        client = _client()
        resp = client.post(
            "/api/local-models/registry/add",
            data={"model_id": "  phi3:mini  ", "provider": "  ollama  "},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"] == {"model_id": "phi3:mini", "provider": "ollama"}


# -- add: rejected input ------------------------------------------------------

def test_add_duplicate_returns_400():
    with _registry():
        client = _client()
        first = client.post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        assert first.status_code == 200, first.text
        dup = client.post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        assert dup.status_code == 400, dup.text
        # The duplicate must not have produced a second entry.
        listing = client.get("/api/local-models").json()
        matches = [
            m for m in listing["registry"]
            if m["model_id"] == "llama3.2:3b" and m["provider"] == "ollama"
        ]
        assert len(matches) == 1, listing


def test_add_blank_model_id_returns_400():
    with _registry():
        resp = _client().post(
            "/api/local-models/registry/add",
            data={"model_id": "   ", "provider": "ollama"},
        )
        assert resp.status_code == 400, resp.text


def test_add_blank_provider_returns_400():
    with _registry():
        resp = _client().post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": ""},
        )
        assert resp.status_code == 400, resp.text


def test_add_oversized_model_id_returns_400():
    with _registry() as path:
        resp = _client().post(
            "/api/local-models/registry/add",
            data={"model_id": "x" * (MAX_MODEL_ID_LEN + 1), "provider": "ollama"},
        )
        assert resp.status_code == 400, resp.text
        # Nothing was persisted.
        assert not os.path.exists(path), path


def test_add_oversized_provider_returns_400():
    with _registry():
        resp = _client().post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "y" * (MAX_PROVIDER_LEN + 1)},
        )
        assert resp.status_code == 400, resp.text


# -- remove -------------------------------------------------------------------

def test_remove_returns_ok_and_drops_entry():
    with _registry():
        client = _client()
        client.post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        client.post(
            "/api/local-models/registry/add",
            data={"model_id": "phi3:mini", "provider": "ollama"},
        )
        assert _registry_ids(client) == {"llama3.2:3b", "phi3:mini"}

        resp = client.post(
            "/api/local-models/registry/remove",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}, resp.text

        # The removed entry is gone; the other remains.
        assert _registry_ids(client) == {"phi3:mini"}


def test_remove_nonexistent_entry_returns_400():
    with _registry():
        client = _client()
        client.post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        # Wrong model_id, and right model_id with wrong provider, both 400.
        missing = client.post(
            "/api/local-models/registry/remove",
            data={"model_id": "does-not-exist", "provider": "ollama"},
        )
        assert missing.status_code == 400, missing.text
        wrong_provider = client.post(
            "/api/local-models/registry/remove",
            data={"model_id": "llama3.2:3b", "provider": "mlx"},
        )
        assert wrong_provider.status_code == 400, wrong_provider.text
        # The real entry is untouched.
        assert _registry_ids(client) == {"llama3.2:3b"}


def test_remove_when_no_registry_returns_400():
    # No manifest exists at all -> nothing to remove -> 400.
    with _registry(initial_contents=None) as path:
        assert not os.path.exists(path)
        resp = _client().post(
            "/api/local-models/registry/remove",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
        )
        assert resp.status_code == 400, resp.text


# -- auth gate ----------------------------------------------------------------

def test_add_requires_auth_without_dev_bypass():
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None,
              LOCAL_MODEL_REGISTRY_PATH=None):
        resp = _client().post(
            "/api/local-models/registry/add",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303, 307, 401), resp.status_code
    assert resp.headers.get("location") == "/login", resp.headers


def test_remove_requires_auth_without_dev_bypass():
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None,
              LOCAL_MODEL_REGISTRY_PATH=None):
        resp = _client().post(
            "/api/local-models/registry/remove",
            data={"model_id": "llama3.2:3b", "provider": "ollama"},
            follow_redirects=False,
        )
    assert resp.status_code in (302, 303, 307, 401), resp.status_code
    assert resp.headers.get("location") == "/login", resp.headers


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
