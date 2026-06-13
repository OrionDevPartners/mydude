"""Tests for the JSON Local AI Models endpoint the React dashboard consumes.

The live app serves the React SPA, which reads its Local AI Models panel from the
JSON twin at ``/api/local-models`` (src/web/api/router.py, ``api_local_models``).
The Jinja ``/local-models`` page has its own coverage in
``tests/test_local_models_panel.py``; this suite locks in the JSON contract the
dashboard actually depends on, so a regression in provider detection, the
reachable/total counts, or the registry payload can't slip through unnoticed.

Covered:
  * ``/api/local-models`` returns 200 with JSON exposing both local providers
    (ollama, mlx), a numeric reachable_count/total_count, and a registry array.
  * registry_exists is false and registry is empty when the manifest is absent.
  * registry is populated (and registry_exists true) when
    LOCAL_MODEL_REGISTRY_PATH points at a valid manifest.
  * With DEV_AUTH_BYPASS off (and not in a deployment) the endpoint refuses to
    serve — require_auth raises its 303 -> /login redirect.

We mount the real ``/api`` router (src/web/api/router.py) on a throwaway FastAPI
app — exactly like tests/test_task_run_pipeline.py — so the TestClient exercises
the same handler the SPA hits. Authentication is satisfied via DEV_AUTH_BYPASS so
no login round-trip or cookie is needed. No local server, secret, or network is
required: the TCP availability probe simply finds nothing listening on localhost,
so reachable_count is 0 while total_count still counts both providers.

Runnable two ways:
  * ``python tests/test_api_local_models.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_api_local_models.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api.router import router as api_router


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
def _no_registry():
    """Point LOCAL_MODEL_REGISTRY_PATH at a guaranteed-absent file (bypass on)."""
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "does-not-exist", "model_registry.yaml")
        with _env(LOCAL_MODEL_REGISTRY_PATH=missing, DEV_AUTH_BYPASS="1",
                  REPLIT_DEPLOYMENT=None):
            yield missing


@contextmanager
def _populated_registry(contents: str):
    """Write a registry manifest and point LOCAL_MODEL_REGISTRY_PATH at it."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "model_registry.yaml")
        with open(path, "w") as f:
            f.write(contents)
        with _env(LOCAL_MODEL_REGISTRY_PATH=path, DEV_AUTH_BYPASS="1",
                  REPLIT_DEPLOYMENT=None):
            yield path


def _provider_keys(body) -> set:
    return {p["key"] for p in body["providers"]}


# -- shape + provider detection ----------------------------------------------

def test_returns_both_local_providers_and_counts():
    with _no_registry():
        resp = _client().get("/api/local-models")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Both local providers are detected and surfaced to the dashboard.
    keys = _provider_keys(body)
    assert "ollama" in keys, body
    assert "mlx" in keys, body

    # Counts are present and numeric; total matches the providers array length.
    assert isinstance(body["reachable_count"], int), body
    assert isinstance(body["total_count"], int), body
    assert body["total_count"] == len(body["providers"]), body
    assert body["total_count"] >= 2, body
    # Nothing is listening on localhost in CI, so none are reachable, and the
    # reachable count can never exceed the total.
    assert body["reachable_count"] == 0, body
    assert 0 <= body["reachable_count"] <= body["total_count"], body

    # The registry payload is always an array (empty here, no manifest).
    assert isinstance(body["registry"], list), body


def test_each_provider_block_has_reachable_flag():
    with _no_registry():
        body = _client().get("/api/local-models").json()
    for p in body["providers"]:
        assert "key" in p, p
        assert isinstance(p["reachable"], bool), p
        assert p["reachable"] is False, p  # nothing listening in CI


# -- registry: absent vs populated -------------------------------------------

def test_registry_absent_reports_empty_and_not_exists():
    with _no_registry() as missing:
        body = _client().get("/api/local-models").json()
    assert body["registry_exists"] is False, body
    assert body["registry"] == [], body
    # The endpoint echoes the resolved registry path it probed.
    assert body["registry_path"] == missing, body


def test_registry_populated_when_manifest_present():
    manifest = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
        "    quant: q4_0\n"
        "  - model_id: mlx-community/Qwen3-14B-8bit\n"
        "    provider: mlx\n"
        "    size_gb: 14\n"
    )
    with _populated_registry(manifest) as path:
        body = _client().get("/api/local-models").json()

    assert body["registry_exists"] is True, body
    assert body["registry_path"] == path, body

    registry = body["registry"]
    assert isinstance(registry, list) and len(registry) == 2, registry
    ids = {m["model_id"] for m in registry}
    assert ids == {"llama3.2:3b", "mlx-community/Qwen3-14B-8bit"}, registry
    by_id = {m["model_id"]: m for m in registry}
    assert by_id["llama3.2:3b"]["provider"] == "ollama", registry
    assert by_id["llama3.2:3b"]["quant"] == "q4_0", registry
    assert by_id["mlx-community/Qwen3-14B-8bit"]["provider"] == "mlx", registry


def test_registry_empty_when_manifest_lists_no_models():
    # A valid-but-empty manifest exists: registry_exists is true (the file is
    # there) but the model list is empty.
    with _populated_registry("models: []\n"):
        body = _client().get("/api/local-models").json()
    assert body["registry_exists"] is True, body
    assert body["registry"] == [], body


# -- auth gate ----------------------------------------------------------------

def test_requires_auth_without_dev_bypass():
    # With DEV_AUTH_BYPASS off (and not in a deployment), require_auth raises its
    # 303 -> /login redirect rather than serving the JSON.
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None,
              LOCAL_MODEL_REGISTRY_PATH=None):
        resp = _client().get("/api/local-models", follow_redirects=False)
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
