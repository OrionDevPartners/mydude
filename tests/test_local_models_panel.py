"""Tests for the Local AI Models status panel.

Covers the /local-models console end-to-end (route logic + Jinja rendering)
*minus* any running local inference server:

  * the page returns 200 and renders both local providers (Ollama, Apple MLX);
  * the guided-setup commands embed the default model names sourced from
    config/providers.toml (llama3.2:3b, mlx-community/Qwen3-14B-8bit), so the
    on-screen guidance can never drift from what the swarm actually resolves;
  * the registry section shows the empty-state warning when the model registry
    file is absent, and renders a populated table when LOCAL_MODEL_REGISTRY_PATH
    points at a valid manifest.

The Jinja `/local-models` route lives in src/web/routes_local_models.py. The
live app mounts the JSON twin at /api/local-models for the React SPA, so we
mount the Jinja router on a throwaway FastAPI app to exercise the template that
the route renders. Authentication is satisfied via DEV_AUTH_BYPASS so no login
round-trip or cookie is needed. No local server, secret, or network is required:
the TCP availability probe simply finds nothing listening on localhost.

Runnable two ways:
  * ``python tests/test_local_models_panel.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_local_models_panel.py``   (test_* functions; no plugins needed)
"""
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.routes_local_models import router as local_models_router

# Default model names declared in config/providers.toml (env_1). The guided-setup
# commands must surface exactly these, since the swarm resolves the same source.
OLLAMA_DEFAULT_MODEL = "llama3.2:3b"
MLX_DEFAULT_MODEL = "mlx-community/Qwen3-14B-8bit"


def _client() -> TestClient:
    """A TestClient over a minimal app that mounts only the Jinja route."""
    app = FastAPI()
    app.include_router(local_models_router)
    return TestClient(app)


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
    """Point LOCAL_MODEL_REGISTRY_PATH at a guaranteed-absent file."""
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "does-not-exist", "model_registry.yaml")
        with _env(LOCAL_MODEL_REGISTRY_PATH=missing, DEV_AUTH_BYPASS="1",
                  REPLIT_DEPLOYMENT=None):
            yield


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


# -- page renders both local providers ----------------------------------------

def test_page_returns_200_and_renders_both_providers():
    with _no_registry():
        resp = _client().get("/local-models")
    assert resp.status_code == 200, resp.status_code
    body = resp.text
    # Friendly labels for each local provider come from _PROVIDER_META.
    assert "Ollama" in body, "Ollama provider card missing"
    assert "Apple MLX" in body, "Apple MLX provider card missing"
    # The summary line counts the local servers (0 reachable in CI).
    assert "local\n                servers reachable" in body or "servers reachable" in body


def test_auth_required_without_dev_bypass():
    # With DEV_AUTH_BYPASS off (and not in a deployment), the route must redirect
    # to /login rather than serve the panel.
    with _env(DEV_AUTH_BYPASS=None, REPLIT_DEPLOYMENT=None,
              LOCAL_MODEL_REGISTRY_PATH=None):
        resp = _client().get("/local-models", follow_redirects=False)
    assert resp.status_code in (302, 303, 307), resp.status_code
    assert resp.headers.get("location") == "/login", resp.headers


# -- guided-setup commands embed the config default models --------------------

def test_pull_commands_use_config_default_models():
    with _no_registry():
        body = _client().get("/local-models").text
    # Ollama pull command, model name injected from spec.default_model.
    assert "ollama pull %s" % OLLAMA_DEFAULT_MODEL in body, body
    # MLX serve-with-model command, model name injected from spec.default_model.
    assert "mlx_lm.server --model %s" % MLX_DEFAULT_MODEL in body, body


def test_install_and_serve_commands_present():
    with _no_registry():
        body = _client().get("/local-models").text
    # Install commands (no model name) for each provider.
    assert "curl -fsSL https://ollama.com/install.sh | sh" in body, body
    assert "pip install mlx-lm" in body, body
    # Serve commands — MLX's carries the resolved port from its default base URL.
    assert "ollama serve" in body, body
    assert "mlx_lm.server --port 11435" in body, body


# -- registry section: empty state vs populated table -------------------------

def test_registry_shows_empty_state_when_absent():
    with _no_registry():
        body = _client().get("/local-models").text
    assert "No registry file found at that path" in body, body
    # The populated-table header must NOT appear when there is no registry.
    assert "<th>Model ID</th>" not in body, body


def test_registry_renders_populated_table():
    manifest = (
        "models:\n"
        "  - model_id: llama3.2:3b\n"
        "    provider: ollama\n"
        "    quant: q4_0\n"
        "  - model_id: mlx-community/Qwen3-14B-8bit\n"
        "    provider: mlx\n"
        "    size_gb: 14\n"
    )
    with _populated_registry(manifest):
        body = _client().get("/local-models").text
    # Table header + both registered model ids appear; empty-state warning gone.
    assert "<th>Model ID</th>" in body, body
    assert "llama3.2:3b" in body, body
    assert "mlx-community/Qwen3-14B-8bit" in body, body
    assert "No registry file found at that path" not in body, body
    # Extra per-model fields render as chips (k=v) in the Details column.
    assert "quant=q4_0" in body, body


def test_registry_empty_when_file_lists_no_models():
    # A valid-but-empty manifest exists -> neither the "absent" warning nor the
    # table; the "found but lists no models" empty-state shows instead.
    with _populated_registry("models: []\n"):
        body = _client().get("/local-models").text
    assert "No registry file found at that path" not in body, body
    assert "<th>Model ID</th>" not in body, body
    assert "Registry file found but it lists no models" in body, body


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
