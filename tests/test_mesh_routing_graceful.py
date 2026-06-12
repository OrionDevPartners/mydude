"""Tests proving Mesh / local-provider routing degrades gracefully.

These are fast, offline unit tests (no network, no running inference server, no
secrets) that pin the behaviour the swarm relies on when a local model node —
localhost or a Cloudflare Mesh peer — is reachable, slow, or completely down:

  * ``_probe_timeout()`` resolves in the documented order on both local
    adapters: provider-specific env (OLLAMA_PROBE_TIMEOUT / MLX_PROBE_TIMEOUT)
    -> shared LOCAL_PROBE_TIMEOUT -> the class DEFAULT_PROBE_TIMEOUT, ignoring
    unparseable values at each tier.
  * ``_local_endpoint_status()`` reports the resolved endpoint (localhost or a
    Mesh IP) and a server_up flag that mirrors a mocked up/down TCP probe, and
    returns ``server_up=None`` when no endpoint is configured.
  * ``provider_exec_locus_distribution()`` carries endpoint + server_up for
    local providers (so operators can see the Mesh link) while leaving them
    ``None`` for cloud providers.
  * When the probe fails, the provider is excluded from the swarm fanout —
    ``OllamaAdapter.is_available()`` is False and its distribution row reports
    ``server_up=False`` — without raising.

Runnable two ways:
  * ``python tests/test_mesh_routing_graceful.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_mesh_routing_graceful.py``   (test_* functions; no plugins needed)
"""
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers import adapters as adapters_mod
from src.providers.adapters import OllamaAdapter, MLXAdapter
from src.providers.config import defined_provider_specs
from src.swarm import jurisdiction as jur


# -- helpers ------------------------------------------------------------------

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


# Probe-timeout env vars that must be cleared so the resolution-order tests start
# from a known baseline regardless of the host environment.
_PROBE_ENV_KEYS = {
    "OLLAMA_PROBE_TIMEOUT": None,
    "MLX_PROBE_TIMEOUT": None,
    "LOCAL_PROBE_TIMEOUT": None,
}


def _spec(key: str):
    return defined_provider_specs()[key]


def _ollama() -> OllamaAdapter:
    return OllamaAdapter(_spec("ollama"))


def _mlx() -> MLXAdapter:
    return MLXAdapter(_spec("mlx"))


# -- _probe_timeout() resolution order ----------------------------------------

def test_probe_timeout_defaults_when_unset():
    # With no env override, both adapters use their class default (localhost-tuned).
    with _env(**_PROBE_ENV_KEYS):
        assert _ollama()._probe_timeout() == OllamaAdapter.DEFAULT_PROBE_TIMEOUT
        assert _mlx()._probe_timeout() == MLXAdapter.DEFAULT_PROBE_TIMEOUT
    # Sanity: the documented localhost default is 0.5 s.
    assert OllamaAdapter.DEFAULT_PROBE_TIMEOUT == 0.5
    assert MLXAdapter.DEFAULT_PROBE_TIMEOUT == 0.5


def test_probe_timeout_shared_env_applies_to_all_local_providers():
    # LOCAL_PROBE_TIMEOUT is the shared fallback when no per-provider var is set.
    with _env(**{**_PROBE_ENV_KEYS, "LOCAL_PROBE_TIMEOUT": "2.0"}):
        assert _ollama()._probe_timeout() == 2.0
        assert _mlx()._probe_timeout() == 2.0


def test_probe_timeout_provider_specific_overrides_shared():
    # Provider-specific var wins over the shared one (Mesh latency tuned per node).
    with _env(**{**_PROBE_ENV_KEYS,
                 "LOCAL_PROBE_TIMEOUT": "2.0",
                 "OLLAMA_PROBE_TIMEOUT": "3.5"}):
        assert _ollama()._probe_timeout() == 3.5
        # MLX has no per-provider override here, so it still uses the shared value.
        assert _mlx()._probe_timeout() == 2.0
    with _env(**{**_PROBE_ENV_KEYS,
                 "LOCAL_PROBE_TIMEOUT": "2.0",
                 "MLX_PROBE_TIMEOUT": "4.0"}):
        assert _mlx()._probe_timeout() == 4.0
        assert _ollama()._probe_timeout() == 2.0


def test_probe_timeout_ignores_unparseable_values():
    # A garbage per-provider value falls through to the shared var...
    with _env(**{**_PROBE_ENV_KEYS,
                 "OLLAMA_PROBE_TIMEOUT": "notanumber",
                 "LOCAL_PROBE_TIMEOUT": "2.0"}):
        assert _ollama()._probe_timeout() == 2.0
    # ...and a garbage shared value falls through to the class default.
    with _env(**{**_PROBE_ENV_KEYS, "LOCAL_PROBE_TIMEOUT": "bogus"}):
        assert _ollama()._probe_timeout() == OllamaAdapter.DEFAULT_PROBE_TIMEOUT


# -- _local_endpoint_status() with mocked up/down probe -----------------------

@contextmanager
def _patched_probe(result):
    """Patch jurisdiction._probe_local_endpoint to a fixed result, capturing args."""
    calls = []
    original = jur._probe_local_endpoint

    def fake(base_url, timeout=0.5):
        calls.append((base_url, timeout))
        return result

    jur._probe_local_endpoint = fake
    try:
        yield calls
    finally:
        jur._probe_local_endpoint = original


def test_local_endpoint_status_server_down_localhost():
    with _env(**{**_PROBE_ENV_KEYS, "OLLAMA_BASE_URL": None}):
        with _patched_probe(False) as calls:
            status = jur._local_endpoint_status(_spec("ollama"))
    assert status["endpoint"] == "http://localhost:11434/v1", status
    assert status["server_up"] is False, status
    # Default localhost probe timeout was passed through.
    assert calls[0][1] == 0.5, calls


def test_local_endpoint_status_server_up_over_mesh_ip():
    mesh_url = "http://100.96.0.1:11434/v1"
    with _env(**{**_PROBE_ENV_KEYS,
                 "OLLAMA_BASE_URL": mesh_url,
                 "OLLAMA_PROBE_TIMEOUT": "2.0"}):
        with _patched_probe(True) as calls:
            status = jur._local_endpoint_status(_spec("ollama"))
    # Endpoint reflects the configured Mesh IP, not localhost.
    assert status["endpoint"] == mesh_url, status
    assert status["server_up"] is True, status
    # The relaxed Mesh probe timeout was honoured.
    assert calls[0] == (mesh_url, 2.0), calls


def test_local_endpoint_status_no_endpoint_skips_probe():
    # A spec with no base URL configured yields server_up=None and no probe call.
    from src.providers.base import ProviderSpec
    blank = ProviderSpec(key="ollama", adapter="ollama_chat")
    with _env(**{**_PROBE_ENV_KEYS, "OLLAMA_BASE_URL": None}):
        with _patched_probe(True) as calls:
            status = jur._local_endpoint_status(blank)
    assert status == {"endpoint": "", "server_up": None}, status
    assert calls == [], "probe must be skipped when no endpoint is configured"


# -- provider_exec_locus_distribution() endpoint/server_up fields -------------

def _by_provider(rows):
    return {r["provider"]: r for r in rows}


def test_distribution_local_rows_carry_endpoint_and_server_up():
    # Force cloud_shift on so cloud providers stay routable too, and mock the
    # local probe as down so we exercise the graceful-degradation path.
    with _env(**{**_PROBE_ENV_KEYS,
                 "CLOUD_SHIFT_ENABLED": "true",
                 "OLLAMA_BASE_URL": None,
                 "MLX_BASE_URL": None}):
        jur._cloud_shift_cache = None  # bust the TTL cache
        with _patched_probe(False):
            rows = jur.provider_exec_locus_distribution()
    by = _by_provider(rows)

    # Local providers: always "available" (no secret), endpoint set, server_up=False.
    for key, ep in (("ollama", "http://localhost:11434/v1"),
                    ("mlx", "http://localhost:11435/v1")):
        row = by[key]
        assert row["exec_locus"] == "local", row
        assert row["available"] is True, row
        assert row["endpoint"] == ep, row
        assert row["server_up"] is False, row

    # Cloud providers: endpoint/server_up stay None (no local probe applies).
    cloud = by["openai"]
    assert cloud["exec_locus"] != "local", cloud
    assert cloud["endpoint"] is None, cloud
    assert cloud["server_up"] is None, cloud


def test_distribution_reports_mesh_endpoint_when_server_up():
    mesh_url = "http://100.96.0.2:11435/v1"
    with _env(**{**_PROBE_ENV_KEYS,
                 "CLOUD_SHIFT_ENABLED": "true",
                 "MLX_BASE_URL": mesh_url}):
        jur._cloud_shift_cache = None
        with _patched_probe(True):
            rows = jur.provider_exec_locus_distribution()
    mlx = _by_provider(rows)["mlx"]
    assert mlx["endpoint"] == mesh_url, mlx
    assert mlx["server_up"] is True, mlx
    # Local providers stay routable independent of cloud egress.
    assert mlx["routable"] is True, mlx


# -- probe failure excludes provider from fanout ------------------------------

@contextmanager
def _patched_server_listening(result):
    """Patch adapters._server_listening (the adapter-side TCP probe)."""
    original = adapters_mod._server_listening
    adapters_mod._server_listening = lambda base_url, timeout=0.3: result
    try:
        yield
    finally:
        adapters_mod._server_listening = original


def test_probe_failure_makes_adapter_unavailable():
    # Server down => is_available() False => the swarm drops it from the fanout.
    adapter = _ollama()
    with _env(**{**_PROBE_ENV_KEYS, "OLLAMA_BASE_URL": None}):
        with _patched_server_listening(False):
            assert adapter.is_available() is False


def test_probe_success_makes_adapter_available():
    # Server reachable => is_available() True (client builds with placeholder key).
    adapter = _ollama()
    with _env(**{**_PROBE_ENV_KEYS, "OLLAMA_BASE_URL": None}):
        with _patched_server_listening(True):
            assert adapter.is_available() is True


def test_probe_failure_does_not_raise_in_distribution():
    # Even if the underlying probe raises, the distribution degrades to a row
    # rather than blowing up the governance dashboard.
    def boom(base_url, timeout=0.5):
        raise OSError("network unreachable")

    original = jur._probe_local_endpoint
    jur._probe_local_endpoint = boom
    try:
        with _env(**{**_PROBE_ENV_KEYS,
                     "CLOUD_SHIFT_ENABLED": "true",
                     "OLLAMA_BASE_URL": None}):
            jur._cloud_shift_cache = None
            rows = jur.provider_exec_locus_distribution()
    finally:
        jur._probe_local_endpoint = original
    ollama = _by_provider(rows)["ollama"]
    # The exception is swallowed; endpoint/server_up stay at their defaults.
    assert ollama["exec_locus"] == "local", ollama
    assert ollama["server_up"] is None, ollama


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
